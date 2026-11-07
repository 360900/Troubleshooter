"""Graphics entries: what they catch, and what a healthy machine must not trip."""
from tests.test_engine import DB, EngineCase
from troubleshooter import engine


class RadeonToAmdgpuTests(EngineCase):
    def hawaii(self, radeon_si="1", amdgpu_si="0"):
        m = self.machine()
        m.pci("0000:01:00.0", "1002", "67b1", "030000", "radeon")  # R9 290
        m.write("/sys/module/radeon/parameters/si_support", radeon_si + "\n")
        m.write("/sys/module/radeon/parameters/cik_support", radeon_si + "\n")
        m.write("/sys/module/amdgpu/parameters/si_support", amdgpu_si + "\n")
        m.write("/sys/module/amdgpu/parameters/cik_support", amdgpu_si + "\n")
        return m

    def evaluate(self, m):
        return engine.evaluate(DB.entries["amd-radeon-to-amdgpu"], m.system())

    def test_older_kernel_defaults_are_not_mistaken_for_a_choice(self):
        # Before 6.19 radeon claims these cards by default and sysfs shows radeon 1, amdgpu 0.
        self.assertTrue(self.evaluate(self.hawaii()).recommended)

    def test_newer_kernel_with_radeon_still_bound_is_caught(self):
        self.assertTrue(self.evaluate(self.hawaii("-1", "-1")).recommended)

    def test_radeon_chosen_with_a_parameter_is_left_alone(self):
        m = self.hawaii()
        m.write("/proc/cmdline", "root=x radeon.cik_support=1\n")
        self.assertEqual(self.evaluate(m).status, engine.FIXED)
        m = self.hawaii()
        m.write("/etc/modprobe.d/amdgpu.conf", "options amdgpu cik_support=0\n")
        self.assertEqual(self.evaluate(m).status, engine.FIXED)

    def test_card_on_amdgpu_does_not_apply(self):
        m = self.machine()
        m.pci("0000:01:00.0", "1002", "67b1", "030000", "amdgpu")
        self.assertEqual(self.evaluate(m).status, engine.NOT_APPLICABLE)
