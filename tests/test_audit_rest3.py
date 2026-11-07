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
