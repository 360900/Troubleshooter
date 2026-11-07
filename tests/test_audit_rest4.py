from tests.test_engine import DB, EngineCase
from troubleshooter import engine
from troubleshooter.system import Result, System


def evaluate(machine, entry_id, system=None):
    return engine.evaluate(DB.entries[entry_id], system or machine.system())


def picked(machine, entry_id, system=None):
    return engine.diagnose_entry(DB.entries[entry_id], system or machine.system())


class FilePickerPortalTests(EngineCase):
    GTK = "[portal]\nDBusName=org.freedesktop.impl.portal.desktop.gtk\nInterfaces=org.freedesktop.impl.portal.FileChooser;org.freedesktop.impl.portal.AppChooser;\n"
    WLR = "[portal]\nDBusName=org.freedesktop.impl.portal.desktop.wlr\nInterfaces=org.freedesktop.impl.portal.Screenshot;org.freedesktop.impl.portal.ScreenCast;\n"

    def steam(self):
        m = self.machine()
        m.packages = {"steam", "xdg-desktop-portal", "xdg-desktop-portal-wlr"}
        m.write("/usr/share/xdg-desktop-portal/portals/wlr.portal", self.WLR)
        return m

    def test_a_backend_without_a_file_picker_does_not_count(self):
        d = evaluate(self.steam(), "steam-file-picker-portal")
        self.assertEqual(d.status, engine.APPLIES)
        self.assertEqual(len(d.found), 1)

    def test_any_backend_with_a_file_picker_is_fixed(self):
        m = self.steam()
        m.write("/usr/share/xdg-desktop-portal/portals/gtk.portal", self.GTK)
        self.assertEqual(evaluate(m, "steam-file-picker-portal").status, engine.FIXED)


class SteamBetaTests(EngineCase):
    def test_the_beta_file_is_the_evidence(self):
        m = self.machine()
        m.packages = {"steam"}
        self.assertFalse(evaluate(m, "steam-client-broken").found)
        (m.home / ".local/share/Steam/package").mkdir(parents=True)
        (m.home / ".local/share/Steam/package/beta").write_text("publicbeta")
        self.assertEqual([f.signal.says for f in evaluate(m, "steam-client-broken").found],
                         ["Steam is set to use a beta client"])


class PackageCacheTests(EngineCase):
    DF = ("df", "--output=pcent", "/var/cache/pacman/pkg")

    def machine_with(self, used):
        m = self.machine()
        m.packages = {"pacman-contrib"}
        m.outputs["services"] = {"paccache.timer": "disabled"}
        m.outputs["commands"] = {self.DF: f"Use%\n {used}\n"}
        return m

    def test_a_disabled_timer_on_a_roomy_disk_is_not_recommended_even_when_picked(self):
        d = picked(self.machine_with("68%"), "updates-package-cache-full")
        self.assertEqual(len(d.found), 1)
        self.assertFalse(d.recommended)

    def test_a_nearly_full_disk_is_recommended(self):
        self.assertTrue(picked(self.machine_with("94%"), "updates-package-cache-full").recommended)
        self.assertTrue(picked(self.machine_with("100%"), "updates-package-cache-full").recommended)

    def test_a_pacman_hook_running_paccache_is_fixed(self):
        m = self.machine_with("94%")
        m.write("/etc/pacman.d/hooks/clean-cache.hook",
                "[Action]\nWhen = PostTransaction\nExec = /usr/bin/paccache -rk2\n")
        self.assertEqual(evaluate(m, "updates-package-cache-full").status, engine.FIXED)


class RebootAfterKernelTests(EngineCase):
    def system(self, machine, modinfo):
        def commands(argv):
            if argv[0] == "modinfo":
                return modinfo
            return machine._commands(argv)
        return System(root=machine.root, home=machine.home, env={}, commands=commands)

    def test_running_kernel_modules_gone_is_recommended(self):
        m = self.machine()
        gone = Result(1, "modinfo: ERROR: Module loop not found.\n")
        self.assertTrue(picked(m, "updates-reboot-after-kernel", self.system(m, gone)).recommended)

    def test_modules_still_there_is_not_blamed_on_the_update(self):
        # A failed DKMS build makes modprobe log the same "not found in directory" line.
        m = self.machine()
        m.journal("systemd-modules-load[312]: modprobe: FATAL: Module nvidia not found in directory /lib/modules/7.3.1-1-cachyos\n")
        d = evaluate(m, "updates-reboot-after-kernel", self.system(m, Result(0, "loop\n")))
        self.assertEqual(len(d.found), 1)
        self.assertFalse(d.recommended)

    def test_without_modinfo_nothing_is_assumed(self):
        m = self.machine()
        d = evaluate(m, "updates-reboot-after-kernel", self.system(m, Result(127, "")))
        self.assertFalse(d.found)


class AudioGroupTests(EngineCase):
    def group(self, members):
        m = self.machine()
        m.packages = {"pipewire-pulse"}
        m.write("/etc/group", f"wheel:x:998:tarik\naudio:x:995:{members}\n")
        return evaluate(m, "users-audio-group")

    def test_daemons_that_belong_there_are_ignored(self):
        self.assertFalse(self.group("brltty").found)
        self.assertFalse(self.group("mpd,brltty").found)

    def test_a_user_is_found(self):
        self.assertTrue(self.group("brltty,tarik").found)
        self.assertTrue(self.group("tarik").found)


class RegdomTests(EngineCase):
    SELF_MANAGED = ("global\ncountry 00: DFS-UNSET\n\t(2402 - 2472 @ 40), (6, 20), (N/A)\n\n"
                    "phy#0 (self-managed)\ncountry TR: DFS-ETSI\n\t(2402 - 2482 @ 40), (6, 22), (N/A)\n")
    WORLD = "global\ncountry 00: DFS-UNSET\n\t(2402 - 2472 @ 40), (6, 20), (N/A)\n"

    def wifi(self, iw=None):
        m = self.machine()
        m.write("/etc/conf.d/wireless-regdom", '#WIRELESS_REGDOM="DE"\n#WIRELESS_REGDOM="TR"\n')
        m.write("/sys/class/ieee80211/phy0/index", "0\n")
        m.modules("mt7925e")
        if iw is not None:
            m.outputs["commands"] = {("iw", "reg", "get"): iw}
        return m

    def test_country_from_the_chip_firmware_is_fixed(self):
        # What an mt7925e reports with the file left untouched.
        self.assertEqual(evaluate(self.wifi(self.SELF_MANAGED), "wifi-regdom-unset").status, engine.FIXED)

    def test_world_domain_in_use_is_recommended_when_picked(self):
        d = picked(self.wifi(self.WORLD), "wifi-regdom-unset")
        self.assertEqual(len(d.found), 3)
        self.assertTrue(d.recommended)

    def test_kernel_parameter_is_fixed(self):
        m = self.wifi()
        m.write("/proc/cmdline", "quiet rw cfg80211.ieee80211_regdom=DE\n")
        self.assertEqual(evaluate(m, "wifi-regdom-unset").status, engine.FIXED)

    def test_without_iw_the_file_still_counts(self):
        d = evaluate(self.wifi(), "wifi-regdom-unset")
        self.assertEqual(d.status, engine.APPLIES)
        self.assertEqual(len(d.found), 2)


class DualBootTests(EngineCase):
    VARS = "/sys/firmware/efi/efivars"
    GUID = "8be4df61-93ca-11d2-aa0d-00e098032b8c"

    def boot_entry(self, m, number, name):
        data = b"\x07\x00\x00\x00\x01\x00\x00\x00\x74\x00" + name.encode("utf-16le") + b"\x00\x00\x04\x01"
        m.write(f"{self.VARS}/Boot{number}-{self.GUID}", data.decode("latin-1"))

    def test_windows_found_from_its_firmware_boot_entry(self):
        # The ESP itself is often root-only, so the file check alone misses Windows.
        m = self.machine()
        self.boot_entry(m, "0000", "Limine")
        self.assertEqual(evaluate(m, "time-dual-boot-offset").status, engine.NOT_APPLICABLE)
        self.boot_entry(m, "0001", "Windows Boot Manager")
        d = evaluate(m, "time-dual-boot-offset")
        self.assertEqual(d.status, engine.APPLIES)
        self.assertTrue(d.found)


class UdevGroupTests(EngineCase):
    def test_both_quotings_are_matched(self):
        for line in ("/usr/lib/udev/rules.d/50-x.rules:3 Failed to resolve group 'plugdev', ignoring: Unknown group",
                     'sdb: /etc/udev/rules.d/99-x.rules:1 Failed to resolve group "plugdev", ignoring: Unknown group'):
            with self.subTest(line=line):
                m = self.machine()
                m.journal(f"systemd-udevd[401]: {line}\n")
                self.assertTrue(evaluate(m, "udev-unknown-group").found)


class OwnConfigTests(EngineCase):
    def test_switch_on_connect_set_up_by_hand_is_fixed(self):
        m = self.machine()
        m.packages = {"pipewire-pulse"}
        (m.home / ".config/pipewire/pipewire-pulse.conf.d").mkdir(parents=True)
        (m.home / ".config/pipewire/pipewire-pulse.conf.d/switch.conf").write_text(
            'pulse.cmd = [\n  { cmd = "load-module" args = "module-switch-on-connect" }\n]\n')
        self.assertEqual(evaluate(m, "sound-switch-on-connect").status, engine.FIXED)

    def test_commented_out_switch_on_connect_is_not_fixed(self):
        m = self.machine()
        m.packages = {"pipewire-pulse"}
        m.write("/etc/pipewire/pipewire-pulse.conf.d/x.conf",
                '#pulse.cmd = [ { cmd = "load-module" args = "module-switch-on-connect" } ]\n')
        self.assertEqual(evaluate(m, "sound-switch-on-connect").status, engine.APPLIES)

    def test_larger_buffers_set_up_by_hand_are_fixed(self):
        m = self.machine()
        m.packages = {"wireplumber"}
        m.write("/etc/wireplumber/wireplumber.conf.d/alsa-config.conf",
                "monitor.alsa.rules = [ { actions = { update-props = { api.alsa.headroom = 8192 } } } ]\n")
        self.assertEqual(evaluate(m, "sound-pipewire-underflow").status, engine.FIXED)

    def test_xbox_receiver_with_autosuspend_off_in_a_dropin_is_fixed(self):
        m = self.machine()
        m.write("/sys/bus/usb/devices/1-2/idVendor", "045e\n")
        m.write("/sys/bus/usb/devices/1-2/idProduct", "0719\n")
        m.outputs["services"] = {"tlp.service": "enabled"}
        self.assertEqual(evaluate(m, "xbox360-receiver-tlp").status, engine.APPLIES)
        m.write("/etc/tlp.d/50-usb.conf", 'USB_AUTOSUSPEND="0"\n')
        self.assertEqual(evaluate(m, "xbox360-receiver-tlp").status, engine.FIXED)
