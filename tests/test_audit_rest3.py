from tests.test_engine import DB, EngineCase
from troubleshooter import engine


class NvidiaModesetOptionTests(EngineCase):
    def evaluate(self, entry, param, value):
        m = self.machine()
        m.modules("nvidia", "nvidia_modeset")
        m.write(f"/sys/module/nvidia_modeset/parameters/{param}", value)
        return engine.evaluate(DB.entries[entry], m.system())

    def test_deep_color_turned_off_any_way_is_fixed(self):
        self.assertEqual(self.evaluate("nvidia-hdmi-deepcolor", "hdmi_deepcolor", "N\n").status, engine.FIXED)
        self.assertEqual(self.evaluate("nvidia-hdmi-deepcolor", "hdmi_deepcolor", "Y\n").status, engine.APPLIES)

    def test_vrr_hidden_through_modprobe_is_fixed(self):
        self.assertEqual(self.evaluate("nvidia-vrr-flip-timeout", "conceal_vrr_caps", "Y\n").status, engine.FIXED)
        self.assertEqual(self.evaluate("nvidia-vrr-flip-timeout", "conceal_vrr_caps", "N\n").status, engine.APPLIES)


class NvidiaHibernateTests(EngineCase):
    SWAPS = "Filename\t\t\t\tType\t\tSize\t\tUsed\t\tPriority\n"

    def evaluate(self, swaps, journal=""):
        m = self.machine()
        m.modules("nvidia")
        m.write("/etc/mkinitcpio.conf.d/10-chwd.conf", "MODULES+=(nvidia nvidia_modeset nvidia_uvm nvidia_drm)\n")
        m.write("/proc/swaps", self.SWAPS + swaps)
        m.journal(journal)
        return engine.evaluate(DB.entries["nvidia-hibernate-early-kms"], m.system())

    def test_zram_only_can_not_hibernate(self):
        d = self.evaluate("/dev/zram0                              partition\t32496636\t0\t100\n")
        self.assertEqual(d.status, engine.NOT_APPLICABLE)

    def test_disk_swap_with_early_loading_applies(self):
        d = self.evaluate("/swap/swapfile                          file\t\t16777212\t0\t-2\n")
        self.assertEqual(d.status, engine.APPLIES)
        self.assertEqual(len(d.found), 1)
        self.assertFalse(d.recommended)

    def test_a_hibernation_attempt_adds_to_it(self):
        d = self.evaluate("/dev/nvme0n1p3                          partition\t16777212\t0\t-2\n",
                          "kernel: PM: hibernation: hibernation entry\n")
        self.assertEqual(len(d.found), 2)
