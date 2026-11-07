"""Checks for entries reviewed in the database audit (group rest1)."""
from tests.test_engine import DB, EngineCase
from troubleshooter import engine, patches


def plan_for(machine, entry_id):
    entry = DB.entries[entry_id]
    system = machine.system()
    fix = next(f for f in entry.fixes if system.in_scopes(f.systems))
    return patches.build_plan(entry.id, entry.title, fix.steps, fix.reboot, system)


class AmdGfxoffTests(EngineCase):
    def gfxoff(self, cmdline="root=x quiet"):
        m = self.machine()
        m.modules("amdgpu")
        m.write("/proc/cmdline", cmdline + "\n")
        return m, engine.diagnose_entry(DB.entries["amd-gfxoff-freeze"], m.system())

    def test_fix_keeps_the_default_mask_minus_gfxoff(self):
        # 0xffff7fff would also turn on Overdrive (0x4000), which taints the kernel.
        m, _ = self.gfxoff()
        m.write("/etc/kernel/cmdline", "root=x quiet\n")
        script = plan_for(m, "amd-gfxoff-freeze").script
        self.assertIn("amdgpu.ppfeaturemask=0xfff73fff", script)
        self.assertNotIn("0xffff7fff", script)
        self.assertEqual(0xfff7bfff & ~0x8000, 0xfff73fff)

    def test_any_mask_without_gfxoff_is_fixed(self):
        for mask in ("0xfff73fff", "0xffff7fff", "0xfffd7fff"):
            with self.subTest(mask=mask):
                self.assertEqual(self.gfxoff(f"root=x amdgpu.ppfeaturemask={mask}")[1].status, engine.FIXED)

    def test_own_mask_with_gfxoff_is_left_alone(self):
        d = self.gfxoff("root=x amdgpu.ppfeaturemask=0xffffffff")[1]
        self.assertEqual(d.status, engine.NOT_APPLICABLE)

    def test_no_mask_applies(self):
        self.assertEqual(self.gfxoff()[1].status, engine.APPLIES)


class AmdAbmTests(EngineCase):
    def abm(self, savings=None, packages=()):
        m = self.machine()
        m.modules("amdgpu")
        m.write("/sys/class/dmi/id/chassis_type", "10\n")
        m.packages = set(packages)
        if savings is not None:
            m.write("/sys/class/drm/card1-eDP-1/amdgpu/panel_power_savings", f"{savings}\n")
        return engine.diagnose_entry(DB.entries["amd-abm-washed-out"], m.system())

    def test_panel_power_savings_in_use_is_evidence(self):
        self.assertGreaterEqual(self.abm(3).confidence, engine.RECOMMEND_AT)

    def test_power_profiles_daemon_alone_is_not_evidence(self):
        # Its amdgpu_panel_power action is off by default since 0.30.
        d = self.abm(0, {"power-profiles-daemon"})
        self.assertFalse(d.found)
        self.assertLess(d.confidence, engine.RECOMMEND_AT)
