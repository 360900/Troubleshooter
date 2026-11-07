import os
import unittest

from tests.test_engine import DB, EngineCase
from troubleshooter import engine


class DesktopCase(EngineCase):
    def evaluate(self, entry_id, m, env=None):
        system = m.system()
        system.env = env or {}
        return engine.evaluate(DB.entries[entry_id], system)


class JavaTests(DesktopCase):
    def java(self, default_target):
        m = self.machine()
        m.packages = {"java-runtime-common", "jre-openjdk"}
        m.write("/usr/lib/jvm/java-25-openjdk/bin/java", "")
        for link in ("default", "default-runtime"):
            os.symlink(default_target, m.root / "usr/lib/jvm" / link)
        return self.evaluate("java-default-broken", m)

    def test_links_left_on_dev_null_are_found(self):
        # Removing the default Java leaves both links pointing at /dev/null, as packaged.
        self.assertTrue(self.java("/dev/null").recommended)

    def test_working_links_are_fixed(self):
        self.assertEqual(self.java("java-25-openjdk").status, engine.FIXED)


class MissingPackageTests(DesktopCase):
    def test_git_builds_count_as_installed(self):
        cases = [
            ("kde-powerdevil-missing", {"plasma-workspace", "powerdevil-git"}, "/usr/lib/org_kde_powerdevil", "KDE"),
            ("portal-kde-missing", {"plasma-workspace", "xdg-desktop-portal-kde-git"},
             "/usr/share/xdg-desktop-portal/portals/kde.portal", "KDE"),
            ("portal-gnome-missing", {"gnome-shell", "xdg-desktop-portal-gnome-git"},
             "/usr/share/xdg-desktop-portal/portals/gnome.portal", "GNOME"),
            ("xfce-gvfs-missing", {"thunar", "gvfs-git"}, "/usr/lib/gvfsd", "XFCE"),
        ]
        for entry_id, packages, path, desktop in cases:
            with self.subTest(entry=entry_id):
                m = self.machine()
                m.packages = packages
                env = {"XDG_CURRENT_DESKTOP": desktop}
                self.assertTrue(self.evaluate(entry_id, m, env).recommended)
                m.write(path, "")
                self.assertEqual(self.evaluate(entry_id, m, env).status, engine.FIXED)

    def test_thunar_without_gvfs_is_only_recommended_in_xfce(self):
        m = self.machine()
        m.packages = {"thunar"}
        self.assertFalse(self.evaluate("xfce-gvfs-missing", m, {"XDG_CURRENT_DESKTOP": "Hyprland"}).recommended)
        self.assertTrue(self.evaluate("xfce-gvfs-missing", m, {"XDG_CURRENT_DESKTOP": "XFCE"}).recommended)


class WineMonoTests(DesktopCase):
    def wine(self):
        m = self.machine()
        m.packages = {"wine"}
        return m

    def test_wine_that_never_ran_is_not_recommended(self):
        # It offers to download Mono the first time it sets up a prefix.
        d = self.evaluate("wine-mono-missing", self.wine())
        self.assertEqual(d.status, engine.APPLIES)
        self.assertFalse(d.recommended)

    def test_a_prefix_set_up_without_mono_is_recommended(self):
        m = self.wine()
        (m.home / ".wine").mkdir()
        (m.home / ".wine/system.reg").write_text("")
        self.assertTrue(self.evaluate("wine-mono-missing", m).recommended)

    def test_a_shared_mono_from_any_package_is_fixed(self):
        m = self.wine()
        m.write("/usr/share/wine/mono/wine-mono-11.3.0/lib/mono.dll", "")
        self.assertEqual(self.evaluate("wine-mono-missing", m).status, engine.FIXED)


class SteamLocaleTests(DesktopCase):
    def steam(self, locales, locale_gen="#en_US.UTF-8 UTF-8\n"):
        m = self.machine()
        m.packages = {"steam"}
        m.write("/etc/locale.gen", locale_gen)
        m.outputs["commands"] = {("locale", "-a"): locales}
        return self.evaluate("steam-locale-missing", m)

    def test_missing_locale_is_recommended(self):
        self.assertTrue(self.steam("C\nC.utf8\nde_DE.utf8\nPOSIX\n").recommended)

    def test_enabled_but_never_generated_is_still_missing(self):
        self.assertTrue(self.steam("C\nC.utf8\nPOSIX\n", "en_US.UTF-8 UTF-8\n").recommended)

    def test_generated_locale_is_fixed_however_it_got_there(self):
        self.assertEqual(self.steam("C\nen_US.utf8\nPOSIX\n").status, engine.FIXED)
        self.assertEqual(self.steam("C\nen_US.UTF-8\nPOSIX\n").status, engine.FIXED)  # glibc-locales


class TlpTests(DesktopCase):
    def tlp(self, ppd):
        m = self.machine()
        m.packages = {"tlp", "power-profiles-daemon"}
        m.outputs["services"] = {"tlp.service": "enabled", "power-profiles-daemon.service": ppd}
        return self.evaluate("power-tlp-ppd-conflict", m)

    def test_running_ppd_is_recommended(self):
        self.assertTrue(self.tlp("active").recommended)

    def test_ppd_that_isnt_running_is_not_recommended(self):
        d = self.tlp("disabled")
        self.assertEqual(d.status, engine.APPLIES)
        self.assertFalse(d.recommended)

    def test_masked_ppd_is_fixed(self):
        self.assertEqual(self.tlp("masked").status, engine.FIXED)


class PodmanTests(DesktopCase):
    def test_missing_subgid_ranges_are_found(self):
        m = self.machine()
        m.packages = {"podman"}
        m.write("/etc/subuid", "alice:100000:65536\n")
        m.write("/etc/subgid", "")
        self.assertTrue(self.evaluate("podman-subuid-missing", m).recommended)
        m.write("/etc/subgid", "alice:100000:65536\n")
        self.assertFalse(self.evaluate("podman-subuid-missing", m).recommended)


class VirtualBoxTests(DesktopCase):
    UNAVAILABLE_KEY = "kernel: Loading of module with unavailable key is rejected\n"
    REJECTED = "systemd-modules-load[312]: Failed to insert module 'vboxdrv': Key was rejected by service\n"
    NOT_BUILT = "systemd-modules-load[312]: Failed to find module 'vboxdrv'\n"

    def vbox(self, journal="", sig_enforce="N"):
        m = self.machine()
        m.packages = {"virtualbox", "virtualbox-host-dkms"}
        m.modules("snd_hda_intel")
        m.write("/sys/module/module/parameters/sig_enforce", sig_enforce + "\n")
        m.write("/sys/kernel/security/lockdown", "[none] integrity confidentiality\n")
        m.journal(journal)
        system = m.system()
        return {e: engine.evaluate(DB.entries[e], system) for e in ("vbox-driver-not-loaded", "vbox-module-unsigned")}

    def test_not_loaded_after_install_is_the_driver(self):
        d = self.vbox()
        self.assertTrue(d["vbox-driver-not-loaded"].recommended)
        self.assertFalse(d["vbox-module-unsigned"].recommended)

    def test_dkms_key_the_kernel_doesnt_trust_is_the_signature(self):
        # DKMS signs with its own generated key, which the kernel reports as unavailable.
        for journal in (self.UNAVAILABLE_KEY, self.REJECTED):
            with self.subTest(journal=journal):
                d = self.vbox(journal)
                self.assertTrue(d["vbox-module-unsigned"].recommended)
                self.assertFalse(d["vbox-driver-not-loaded"].recommended)

    def test_enforced_signing_is_found_without_the_log(self):
        d = self.vbox(sig_enforce="Y")
        self.assertTrue(d["vbox-module-unsigned"].recommended)
        self.assertFalse(d["vbox-driver-not-loaded"].recommended)

    def test_module_not_built_for_this_kernel_isnt_loaded_by_hand(self):
        self.assertFalse(self.vbox(self.NOT_BUILT)["vbox-driver-not-loaded"].recommended)


class MacBookTests(DesktopCase):
    def mac(self):
        m = self.machine()
        m.write("/sys/class/dmi/id/product_name", "MacBookPro14,3\n")
        return m

    def test_fn_remap_set_anywhere_is_fixed(self):
        m = self.mac()
        self.assertTrue(self.evaluate("mbp-keyboard-fn-ctrl", m).recommended)
        m.write("/sys/module/applespi/parameters/fnremap", "1\n")
        self.assertEqual(self.evaluate("mbp-keyboard-fn-ctrl", m).status, engine.FIXED)

    def test_iso_layout_set_anywhere_is_fixed(self):
        m = self.mac()
        m.write("/etc/vconsole.conf", 'KEYMAP="de-latin1"\n')
        self.assertTrue(self.evaluate("mbp-keyboard-iso", m).recommended)
        m.write("/sys/module/applespi/parameters/iso_layout", "Y\n")
        self.assertEqual(self.evaluate("mbp-keyboard-iso", m).status, engine.FIXED)

    def test_us_variants_are_not_iso(self):
        m = self.mac()
        m.write("/etc/vconsole.conf", "KEYMAP=us-acentos\n")
        self.assertFalse(self.evaluate("mbp-keyboard-iso", m).recommended)

    def test_windows_keys_in_any_order_are_fixed(self):
        m = self.mac()
        (m.home / ".config").mkdir()
        (m.home / ".config/kxkbrc").write_text("[Layout]\nOptions=lv3:rwin_switch,altwin:swap_lalt_lwin\n")
        d = self.evaluate("mbp-keyboard-windows-keys", m, {"XDG_CURRENT_DESKTOP": "KDE"})
        self.assertEqual(d.status, engine.FIXED)

    def test_benign_nvme_lines_are_not_ssd_trouble(self):
        m = self.mac()
        m.journal("kernel: nvme nvme0: Shutdown timeout set to 10 seconds\n")
        self.assertEqual(len(self.evaluate("mbp-sleep-fixes", m).found), 1)
        m.journal("kernel: nvme nvme0: I/O tag 12 (100c) opcode 0x2 (Read) QID 1 timeout, aborting\n")
        self.assertEqual(len(self.evaluate("mbp-sleep-fixes", m).found), 2)


if __name__ == "__main__":
    unittest.main()
