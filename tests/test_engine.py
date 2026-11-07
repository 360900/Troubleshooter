import unittest

from tests.fakes import CACHY, DEBIAN, FakeMachine
from troubleshooter import engine
from troubleshooter.database import load_database
from troubleshooter.search import SearchIndex

DB = load_database()


class EngineCase(unittest.TestCase):
    def machine(self, release=CACHY):
        machine = FakeMachine(release)
        self.addCleanup(machine.close)
        return machine

    def scan_ids(self, machine):
        return {d.entry.id for d in engine.scan(DB, machine.system())}


class AutoDetectTests(EngineCase):
    def test_nouveau_on_modern_card_recommends_nvidia_open_for_games(self):
        m = self.machine()
        m.pci("0000:01:00.0", "10de", "2484", "030000", "nouveau")  # RTX 3070
        m.modules("nouveau")
        m.packages = {"steam"}
        self.assertEqual(self.scan_ids(m), {"nvidia-nouveau-to-open"})

    def test_nouveau_alone_is_a_fine_choice_on_modern_cards(self):
        # It reclocks through GSP and has Vulkan since 6.18, so the choice may be deliberate.
        m = self.machine()
        m.pci("0000:01:00.0", "10de", "2484", "030000", "nouveau")
        m.modules("nouveau")
        self.assertEqual(self.scan_ids(m), set())
        m.journal("kernel: nouveau 0000:01:00.0: fifo: fault 01 [WRITE] at 0000000000a1b000 engine 40 [gr]\n")
        self.assertEqual(self.scan_ids(m), {"nvidia-nouveau-to-open"})

    def test_nvidia_open_on_pascal_recommends_580xx(self):
        m = self.machine()
        m.pci("0000:01:00.0", "10de", "1c03", "030000")  # GTX 1060
        m.packages = {"nvidia-open-dkms", "nvidia-utils"}
        results = engine.scan(DB, m.system())
        self.assertEqual([d.entry.id for d in results], ["nvidia-open-on-legacy"])
        self.assertGreaterEqual(results[0].confidence, 90)

    def test_cachyos_kernel_module_package_on_pascal_is_caught(self):
        m = self.machine()
        m.pci("0000:01:00.0", "10de", "1b80", "030000")  # GTX 1080
        m.packages = {"linux-cachyos-nvidia-open", "nvidia-utils"}
        self.assertIn("nvidia-open-on-legacy", self.scan_ids(m))

    def test_580xx_already_installed_is_left_alone(self):
        m = self.machine()
        m.pci("0000:01:00.0", "10de", "1c03", "030000", "nvidia")
        m.packages = {"nvidia-580xx-dkms", "nvidia-580xx-utils"}
        m.modules("nvidia")
        self.assertNotIn("nvidia-open-on-legacy", self.scan_ids(m))

    def test_turing_is_not_legacy(self):
        m = self.machine()
        m.pci("0000:01:00.0", "10de", "1f82", "030000", "nvidia")  # GTX 1650
        m.packages = {"nvidia-open-dkms"}
        m.modules("nvidia")
        self.assertNotIn("nvidia-open-on-legacy", self.scan_ids(m))

    def test_nvidia_without_preserve_memory_recommends_sleep_fix(self):
        m = self.machine()
        m.modules("nvidia")
        m.write("/proc/driver/nvidia/params", "PreserveVideoMemoryAllocations: 0\nUseKernelSuspendNotifiers: 0\n")
        self.assertIn("nvidia-sleep", self.scan_ids(m))

    def test_kernel_suspend_notifiers_mean_sleep_is_fine(self):
        """What this repo's author's RTX 5080 reports: services off, and that's correct."""
        m = self.machine()
        m.modules("nvidia")
        m.write("/proc/driver/nvidia/params", "PreserveVideoMemoryAllocations: 2\nUseKernelSuspendNotifiers: 1\n")
        self.assertNotIn("nvidia-sleep", self.scan_ids(m))

    def test_preserve_on_with_services_is_fine(self):
        m = self.machine()
        m.modules("nvidia")
        m.write("/proc/driver/nvidia/params", "PreserveVideoMemoryAllocations: 1\nUseKernelSuspendNotifiers: 0\n")
        m.outputs["services"] = {"nvidia-suspend.service": "enabled", "nvidia-resume.service": "enabled"}
        self.assertNotIn("nvidia-sleep", self.scan_ids(m))

    def test_broadcom_without_driver_recommends_wl(self):
        m = self.machine()
        m.pci("0000:03:00.0", "14e4", "43a0", "028000")
        self.assertEqual(self.scan_ids(m), {"wifi-broadcom-wl"})

    def test_broadcom_chip_this_entry_does_not_cover_is_ignored(self):
        m = self.machine()
        m.pci("0000:03:00.0", "14e4", "43ba", "028000")  # BCM43602 uses brcmfmac, not wl
        self.assertEqual(self.scan_ids(m), set())

    def test_quiet_mediatek_is_not_flagged_without_log_errors(self):
        m = self.machine()
        m.modules("mt7925e")
        m.journal("kernel: mt7925e 0000:08:00.0: ASIC revision: 79250000\n")
        self.assertEqual(self.scan_ids(m), set())

    def test_mediatek_timeouts_are_flagged(self):
        m = self.machine()
        m.modules("mt7925e")
        m.journal("kernel: mt7925e 0000:08:00.0: Message 00020003 (seq 12) timeout\n")
        self.assertEqual(self.scan_ids(m), {"wifi-mediatek-aspm"})

    def test_clean_machine_has_no_recommendations(self):
        self.assertEqual(self.scan_ids(self.machine()), set())

    def test_scan_skips_entries_for_other_distros(self):
        m = self.machine(DEBIAN)
        m.pci("0000:01:00.0", "10de", "2484", "030000", "nouveau")
        m.modules("nouveau")
        self.assertEqual(self.scan_ids(m), set())


class CachyOSTests(EngineCase):
    ZEN4 = "vendor_id\t: AuthenticAMD\nflags\t\t: fpu avx2 bmi2 fma movbe avx512f avx512bw avx512cd avx512dq avx512vl\n"
    HASWELL = "vendor_id\t: GenuineIntel\nflags\t\t: fpu avx2 bmi2 fma movbe\n"
    OLD = "vendor_id\t: GenuineIntel\nflags\t\t: fpu sse4_2 avx\n"

    def repos(self, m, level, cpu):
        m.write("/etc/pacman.conf", f"[options]\n[cachyos-{level}] \n[cachyos-core-{level}]\n[cachyos]\n[core]\n")
        m.write("/proc/cpuinfo", cpu)
        return "cachyos-repo-unsupported-cpu" in self.scan_ids(m)

    def test_repos_the_cpu_can_run_are_fine(self):
        for level, cpu in (("znver4", self.ZEN4), ("v4", self.ZEN4), ("v3", self.ZEN4), ("v3", self.HASWELL)):
            self.assertFalse(self.repos(self.machine(), level, cpu), (level, cpu))

    def test_repos_built_for_a_newer_cpu_are_caught(self):
        for level, cpu in (("v4", self.HASWELL), ("znver4", self.HASWELL), ("v3", self.OLD),
                           ("znver4", self.ZEN4.replace("AuthenticAMD", "GenuineIntel"))):
            self.assertTrue(self.repos(self.machine(), level, cpu), (level, cpu))

    def nvidia_machine(self, *kernels_with_module):
        m = self.machine()
        m.pci("0000:01:00.0", "10de", "2c02", "030000", "nvidia")
        m.packages = {"nvidia-utils", "linux-cachyos-nvidia-open"}
        for kernel, version in (("linux-cachyos", "7.3.1-1-cachyos"), ("linux-cachyos-lts", "6.18.55-2-cachyos-lts")):
            m.write(f"/usr/lib/modules/{version}/pkgbase", kernel + "\n")
            if kernel in kernels_with_module:
                m.write(f"/usr/lib/modules/{version}/extramodules/nvidia.ko.zst", "")
        return m

    def test_kernel_without_nvidia_module_is_caught(self):
        self.assertIn("nvidia-kernel-missing-driver", self.scan_ids(self.nvidia_machine("linux-cachyos")))
        both = self.nvidia_machine("linux-cachyos", "linux-cachyos-lts")
        self.assertNotIn("nvidia-kernel-missing-driver", self.scan_ids(both))

    def test_dkms_module_counts_and_dkms_setups_are_left_to_the_headers_entry(self):
        m = self.nvidia_machine("linux-cachyos")
        m.write("/usr/lib/modules/6.18.55-2-cachyos-lts/updates/dkms/nvidia.ko.zst", "")
        self.assertEqual(m.system().kernels_without("nvidia"), [])
        m = self.nvidia_machine()
        m.packages = {"nvidia-utils", "nvidia-open-dkms"}
        self.assertNotIn("nvidia-kernel-missing-driver", self.scan_ids(m))


class MacBookProTests(EngineCase):
    """mbp132-setup's parts on a 2016-2017 MacBook Pro, each found on its own."""
    NVRAM = "/usr/lib/firmware/brcm/brcmfmac43602-pcie.Apple Inc.-MacBookPro13,2.txt"
    MBP = {"mbp-audio-driver", "mbp-touchbar-driver", "mbp-sleep-fixes", "mbp-wifi-bcm43602"}

    def mac(self, model="MacBookPro13,2", t1="8600"):
        m = self.machine()
        m.write("/sys/class/dmi/id/sys_vendor", "Apple Inc.\n")
        m.write("/sys/class/dmi/id/product_name", model + "\n")
        m.write("/sys/bus/usb/devices/1-3/idVendor", "05ac\n")
        m.write("/sys/bus/usb/devices/1-3/idProduct", t1 + "\n")
        m.pci("0000:02:00.0", "14e4", "43ba", "028000", "brcmfmac")
        m.write("/sys/class/net/wlp2s0/address", "00:90:4c:0d:f4:3e\n")
        m.write("/proc/asound/card0/codec#0", "Codec: Cirrus Logic CS8409\nAddress: 0\n")
        m.modules("applespi", "brcmfmac", "snd_hda_intel")
        return m

    def mbp_found(self, m, env=None):
        system = m.system()
        system.env = env or {}
        return {d.entry.id for d in engine.scan(DB, system) if d.entry.id.startswith("mbp-")}

    def test_each_part_is_recommended_on_its_own(self):
        # Plus the optional parts that make sense on any of these Macs: the Fn swap, and a
        # backup of the Touch Bar firmware.
        self.assertEqual(self.mbp_found(self.mac()), self.MBP | {"mbp-keyboard-fn-ctrl", "mbp-t1-firmware-backup"})

    def test_iso_fix_is_recommended_with_a_non_us_layout(self):
        m = self.mac()
        self.assertNotIn("mbp-keyboard-iso", self.mbp_found(m))
        m.write("/etc/vconsole.conf", "KEYMAP=trq\n")
        self.assertIn("mbp-keyboard-iso", self.mbp_found(m))
        m.write("/etc/vconsole.conf", "KEYMAP=us\n")
        self.assertNotIn("mbp-keyboard-iso", self.mbp_found(m))

    def test_windows_keys_are_recommended_in_kde_until_set(self):
        m = self.mac()
        kde = {"XDG_CURRENT_DESKTOP": "KDE"}
        self.assertNotIn("mbp-keyboard-windows-keys", self.mbp_found(m))
        self.assertIn("mbp-keyboard-windows-keys", self.mbp_found(m, kde))
        (m.home / ".config").mkdir()
        (m.home / ".config/kxkbrc").write_text("[Layout]\nOptions=altwin:swap_lalt_lwin,lv3:rwin_switch\n")
        self.assertNotIn("mbp-keyboard-windows-keys", self.mbp_found(m, kde))

    def test_grub_entry_is_recommended_when_the_firmware_knows_macos(self):
        m = self.mac()
        m.packages = {"grub"}
        m.write("/etc/default/grub", "")
        path = "\\System\\Library\\CoreServices\\boot.efi"
        m.write("/sys/firmware/efi/efivars/Boot0080-8be4df61-93ca-11d2-aa0d-00e098032b8c",
                "\x07\x00\x00\x00" + "".join(c + "\x00" for c in path))
        self.assertIn("mbp-grub-macos-entry", self.mbp_found(m))

    def test_parts_already_set_up_are_not_recommended(self):
        m = self.mac()
        for path in ("/var/lib/dkms/snd_hda_macbookpro/0.1/source", "/etc/systemd/system/mbp132-volume.service",
                     "/etc/udev/rules.d/40-mbp132-ibridge.rules", "/usr/lib/systemd/system-sleep/mbp132-sleep-fix",
                     "/etc/NetworkManager/dispatcher.d/99-mbp132-wifi-powersave", self.NVRAM):
            m.write(path, "")
        m.write("/sys/class/net/wlp2s0/address", "02:11:22:33:44:55\n")
        m.modules("applespi", "brcmfmac", "snd_hda_intel", "apple_ib_tb")
        system = m.system()
        for entry_id in self.MBP:
            with self.subTest(entry=entry_id):
                self.assertEqual(engine.evaluate(DB.entries[entry_id], system).status, engine.FIXED)

    def test_t1_in_recovery_gets_advice_instead_of_the_driver(self):
        found = self.scan_ids(self.mac(t1="1281"))
        self.assertIn("mbp-t1-recovery", found)
        self.assertNotIn("mbp-touchbar-driver", found)

    def test_wifi_calibration_is_only_required_on_the_13_2(self):
        m = self.mac(model="MacBookPro14,1")
        m.write("/etc/NetworkManager/dispatcher.d/99-mbp132-wifi-powersave", "")
        m.write("/sys/class/net/wlp2s0/address", "02:11:22:33:44:55\n")
        self.assertEqual(engine.evaluate(DB.entries["mbp-wifi-bcm43602"], m.system()).status, engine.FIXED)

    def test_other_computers_are_left_alone(self):
        m = self.machine()
        m.write("/sys/class/dmi/id/product_name", "ThinkPad X1 Carbon\n")
        m.pci("0000:02:00.0", "14e4", "43ba", "028000", "brcmfmac")
        system = m.system()
        for entry in DB.entries.values():
            if entry.id.startswith("mbp-"):
                with self.subTest(entry=entry.id):
                    self.assertEqual(engine.evaluate(entry, system).status, engine.NOT_APPLICABLE)

    def test_generic_bcm43602_fix_stays_off_macs(self):
        self.assertEqual(engine.evaluate(DB.entries["wifi-broadcom-bcm43602"], self.mac().system()).status,
                         engine.NOT_APPLICABLE)


class AvahiTests(EngineCase):
    WARNING = ("avahi-daemon[1247]: *** WARNING: Detected another IPv6 mDNS stack running on this host. "
               "This makes mDNS unreliable and is thus not recommended. ***\n")

    RENAMED = "avahi-daemon[1247]: Host name conflict, retrying with thinx-2\n"

    def avahi(self, mdns, journal=WARNING):
        m = self.machine()
        m.packages = {"avahi"}
        m.outputs["services"] = {"avahi-daemon.service": "active", "systemd-resolved.service": "active"}
        m.outputs["commands"] = {("resolvectl", "mdns"): mdns}
        m.journal(journal)
        return engine.evaluate(DB.entries["avahi-resolved-mdns-conflict"], m.system())

    def test_warning_alone_is_not_recommended_on_launch(self):
        # It only means the setup could conflict, and it's gone once the log rotates.
        d = self.avahi("Global: yes\nLink 3 (wlan0): yes\n")
        self.assertEqual(d.status, engine.APPLIES)
        self.assertEqual(len(d.found), 1)
        self.assertFalse(d.recommended)

    def test_a_renamed_host_is_recommended(self):
        d = self.avahi("Global: yes\nLink 3 (wlan0): yes\n", self.WARNING + self.RENAMED)
        self.assertTrue(d.recommended)
        self.assertTrue(self.avahi("Global: yes\nLink 3 (wlan0): yes\n", self.RENAMED).recommended)

    def test_still_found_by_searching_for_the_symptom(self):
        hits = SearchIndex(DB).search("my computer shows up as hostname-2", 12)
        self.assertIn("avahi-resolved-mdns-conflict", [h.target.id for h in hits if not h.is_category][:3])

    def test_another_app_on_the_mdns_port_is_not_blamed_on_resolved(self):
        # Browsers, Steam or adb holding UDP 5353 make Avahi log the same warning.
        d = self.avahi("Global: yes\nLink 3 (wlan0): no\n")
        self.assertEqual(d.status, engine.NOT_APPLICABLE)

    def test_resolved_limited_to_lookups_is_fixed_wherever_it_was_set(self):
        d = self.avahi("Global: resolve\nLink 3 (wlan0): yes\n")
        self.assertEqual(d.status, engine.FIXED)


class ConfidenceTests(EngineCase):
    def setUp(self):
        self.m = self.machine()
        self.m.modules("amdgpu")
        self.entry = DB.entries["amd-psr-flicker"]

    def test_no_evidence_stays_low_even_when_picked(self):
        d = engine.diagnose_entry(self.entry, self.m.system())
        self.assertEqual(d.status, engine.APPLIES)
        self.assertLess(d.confidence, engine.RECOMMEND_AT)

    def test_evidence_raises_confidence(self):
        before = engine.evaluate(self.entry, self.m.system()).confidence
        self.m.write("/sys/class/dmi/id/chassis_type", "10\n")
        self.m.journal("kernel: amdgpu 0000:05:00.0: [drm] *ERROR* flip_done timed out\n")
        after = engine.evaluate(self.entry, self.m.system()).confidence
        self.assertGreater(after, before)
        self.assertGreaterEqual(after, engine.RECOMMEND_AT)

    def test_reproducing_the_problem_beats_old_logs(self):
        self.m.journal("kernel: amdgpu 0000:05:00.0: flip_done timed out\n")
        before = engine.evaluate(self.entry, self.m.system()).confidence
        self.m.journal("kernel: amdgpu 0000:05:00.0: flip_done timed out\n", window=True)
        system = self.m.system()
        system.cursor = system.journal_cursor()
        d = engine.evaluate(self.entry, system)
        self.assertGreater(d.confidence, before)
        self.assertTrue(any(f.evidence.in_window for f in d.found))

    def test_never_claims_certainty(self):
        self.m.journal("kernel: amdgpu 0000:05:00.0: flip_done timed out\n" * 3, window=True)
        system = self.m.system()
        system.cursor = "x"
        self.assertLessEqual(engine.evaluate(self.entry, system, 0.6).confidence, engine.MAX_CONFIDENCE)

    def test_already_fixed(self):
        self.m.write("/proc/cmdline", "root=x amdgpu.dcdebugmask=0x10\n")
        self.assertEqual(engine.diagnose_entry(self.entry, self.m.system()).status, engine.FIXED)

    def test_missing_hardware_does_not_apply(self):
        m = self.machine()
        self.assertEqual(engine.diagnose_entry(self.entry, m.system()).status, engine.NOT_APPLICABLE)

    def test_wrong_distro(self):
        m = self.machine(DEBIAN)
        m.modules("amdgpu")
        self.assertEqual(engine.diagnose_entry(self.entry, m.system()).status, engine.WRONG_SYSTEM)

    def test_unreadable_source_is_not_evidence(self):
        m = self.machine()
        m.modules("nvidia")
        m.outputs["journal"] = None
        d = engine.evaluate(DB.entries["nvidia-xid"], m.system())
        self.assertFalse(d.found)


class FlowTests(EngineCase):
    def test_category_needs_no_specific_entry(self):
        """Someone whose game flickers can pick Display and get an answer."""
        m = self.machine()
        m.modules("amdgpu")
        m.write("/sys/class/dmi/id/chassis_type", "10\n")
        m.journal("kernel: amdgpu 0000:05:00.0: flip_done timed out\n")
        results = engine.diagnose_category(DB, "display", m.system())
        self.assertEqual(results[0].entry.id, "amd-psr-flicker")
        self.assertTrue(results[0].recommended)

    def test_offers_reproducing_only_when_inconclusive(self):
        m = self.machine()
        m.modules("amdgpu")
        system = m.system()
        results = engine.diagnose_category(DB, "display", system)
        self.assertTrue(engine.can_reproduce(results))
        m.journal("kernel: amdgpu 0000:05:00.0: flip_done timed out\n")
        m.write("/sys/class/dmi/id/chassis_type", "10\n")
        results = engine.diagnose_category(DB, "display", m.system())
        self.assertFalse(engine.can_reproduce(results))

    def test_debian_gets_its_own_fix_variant(self):
        m = self.machine(DEBIAN)
        m.pci("0000:00:1f.3", "8086", "51c8", "040300")
        d = engine.diagnose_entry(DB.entries["sof-firmware-missing"], m.system())
        self.assertIn("firmware-sof-signed", str(d.fix.steps))
