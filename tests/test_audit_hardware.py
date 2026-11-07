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


class XboxAdapterTests(EngineCase):
    def adapter(self, product):
        m = self.machine()
        m.write("/sys/bus/usb/devices/1-2/idVendor", "045e\n")
        m.write("/sys/bus/usb/devices/1-2/idProduct", f"{product}\n")
        return engine.evaluate(DB.entries["xbox-wireless-adapter-driver"], m.system())

    def test_plugged_in_adapter_is_recommended(self):
        self.assertTrue(self.adapter("02fe").recommended)

    def test_built_in_adapter_alone_is_not_recommended_on_launch(self):
        d = self.adapter("091e")
        self.assertEqual(d.status, engine.APPLIES)
        self.assertFalse(d.recommended)


class PipewirePulseTests(EngineCase):
    def test_another_pulseaudio_server_counts_as_fixed(self):
        m = self.machine()
        m.packages = {"pipewire", "pipewire-pulse-git"}
        m.write("/sys/class/sound/card0/number", "0\n")
        m.outputs["commands"] = {("pacman", "-Qq", "pulse-native-provider"): "pipewire-pulse-git\n"}
        system = m.system()
        system.env["XDG_CURRENT_DESKTOP"] = "KDE"
        d = engine.evaluate(DB.entries["sound-pipewire-pulse-missing"], system)
        self.assertEqual(d.status, engine.FIXED)


class IntelWifiTests(EngineCase):
    CRASH = "kernel: iwlwifi 0000:00:14.3: Microcode SW error detected. Restarting 0x0.\n"

    def test_one_old_firmware_crash_is_not_recommended_on_launch(self):
        m = self.machine()
        m.modules("iwlmvm")
        m.journal(self.CRASH)
        d = engine.evaluate(DB.entries["wifi-intel-power"], m.system())
        self.assertEqual(len(d.found), 1)
        self.assertFalse(d.recommended)

    def test_crash_while_reproducing_is_recommended(self):
        m = self.machine()
        m.modules("iwlmvm")
        m.journal(self.CRASH, window=True)
        d = engine.evaluate(DB.entries["wifi-intel-power"], reproduced(m.system()))
        self.assertTrue(d.recommended)


class RealtekTests(EngineCase):
    def rtw89(self, line):
        m = self.machine()
        m.modules("rtw89_pci", "rtw89_core", "rtw89_8852be")
        m.journal(f"kernel: rtw89_8852be 0000:02:00.0: {line}\n")
        return engine.evaluate(DB.entries["wifi-realtek-rtw89"], m.system())

    def test_rtw89_recovered_error_alone_is_not_recommended_on_launch(self):
        d = self.rtw89("SER catches error: 0x1001")
        self.assertEqual(len(d.found), 1)
        self.assertFalse(d.recommended)

    def test_rtw89_chip_stuck_asleep_is_recommended(self):
        self.assertTrue(self.rtw89("failed to leave idle state").recommended)

    def test_rtw89_chip_alone_is_no_evidence(self):
        m = self.machine()
        m.modules("rtw89_pci", "rtw89_8852be")
        self.assertEqual(engine.evaluate(DB.entries["wifi-realtek-rtw89"], m.system()).found, [])

    def test_rtw88_deep_power_saving_off_is_fixed(self):
        m = self.machine()
        m.modules("rtw88_pci", "rtw88_core", "rtw88_8821ce")
        m.write("/sys/module/rtw88_pci/parameters/disable_aspm", "N\n")
        m.write("/sys/module/rtw88_core/parameters/disable_lps_deep", "Y\n")
        d = engine.evaluate(DB.entries["wifi-realtek-rtw88"], m.system())
        self.assertEqual(d.status, engine.FIXED)


if __name__ == "__main__":
    unittest.main()
