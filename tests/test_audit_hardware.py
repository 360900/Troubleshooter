import unittest

from tests.test_engine import DB, EngineCase
from troubleshooter import engine


def reproduced(system):
    system.cursor = system.journal_cursor()
    return system


class RegdbTests(EngineCase):
    def test_missing_database_is_found_after_the_boot_log_rotated(self):
        m = self.machine()
        m.modules("cfg80211")
        d = engine.evaluate(DB.entries["wifi-regdb-missing"], m.system())
        self.assertTrue(d.recommended)

    def test_database_installed_some_other_way_is_fixed(self):
        m = self.machine()
        m.modules("cfg80211")
        m.write("/usr/lib/firmware/regulatory.db", "x")
        d = engine.evaluate(DB.entries["wifi-regdb-missing"], m.system())
        self.assertEqual(d.status, engine.FIXED)


class SofTests(EngineCase):
    def sof(self, driver, modules=()):
        m = self.machine()
        m.pci("0000:00:1f.3", "8086", "a0c8", "040100", driver)
        m.modules(*modules)
        return m

    def test_sof_driver_without_firmware_is_found_without_logs(self):
        m = self.sof("sof-audio-pci-intel-tgl")
        self.assertTrue(engine.evaluate(DB.entries["sof-firmware-missing"], m.system()).recommended)

    def test_plain_hda_with_the_sof_module_loaded_is_not_blamed(self):
        # snd_sof_pci_intel_tgl loads by PCI alias and steps aside for snd_hda_intel.
        m = self.sof("snd_hda_intel", ["snd_sof_pci_intel_tgl", "snd_hda_intel"])
        self.assertFalse(engine.evaluate(DB.entries["sof-firmware-missing"], m.system()).recommended)

    def test_firmware_copied_in_by_hand_is_fixed(self):
        m = self.sof("sof-audio-pci-intel-tgl")
        m.write("/usr/lib/firmware/intel/sof-ipc4/tgl/sof-tgl.ri", "x")
        d = engine.evaluate(DB.entries["sof-firmware-missing"], m.system())
        self.assertEqual(d.status, engine.FIXED)


if __name__ == "__main__":
    unittest.main()
