import unittest

from tests.fakes import CACHY, DEBIAN, FakeMachine
from troubleshooter import engine
from troubleshooter.database import load_database

DB = load_database()


class EngineCase(unittest.TestCase):
    def machine(self, release=CACHY):
        machine = FakeMachine(release)
        self.addCleanup(machine.close)
        return machine

    def scan_ids(self, machine):
        return {d.entry.id for d in engine.scan(DB, machine.system())}


class AutoDetectTests(EngineCase):
    def test_nouveau_on_modern_card_recommends_nvidia_open(self):
        m = self.machine()
        m.pci("0000:01:00.0", "10de", "2484", "030000", "nouveau")  # RTX 3070
        m.modules("nouveau")
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
