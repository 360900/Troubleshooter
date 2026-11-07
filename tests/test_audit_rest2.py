import unittest

from tests.test_engine import DB, EngineCase
from troubleshooter import engine


class AuditCase(EngineCase):
    def evaluate(self, entry_id, machine, env=None):
        system = machine.system()
        system.env = env or {}
        return engine.evaluate(DB.entries[entry_id], system)


class SynapticsTests(AuditCase):
    def test_x11_session_counts(self):
        m = self.machine()
        m.packages = {"xf86-input-synaptics"}
        d = self.evaluate("input-synaptics-driver", m, {"XDG_SESSION_TYPE": "x11"})
        self.assertEqual([f.signal.says for f in d.found], ["This session runs on X11, where the Synaptics driver applies"])
        self.assertFalse(self.evaluate("input-synaptics-driver", m, {"XDG_SESSION_TYPE": "wayland"}).found)


class NetworkingDisabledTests(AuditCase):
    def nm(self, state):
        m = self.machine()
        m.packages = {"networkmanager"}
        m.outputs["commands"] = {("nmcli", "networking"): state}
        return self.evaluate("nm-networking-disabled", m)

    def test_asks_networkmanager_because_its_state_file_is_root_only(self):
        d = self.nm("disabled\n")
        self.assertEqual(d.status, engine.APPLIES)
        self.assertTrue(d.recommended)
        self.assertEqual(self.nm("enabled\n").status, engine.FIXED)


class HibernateTests(AuditCase):
    def test_no_saved_resume_target_is_nothing_to_clear(self):
        m = self.machine()
        m.write("/sys/power/resume", "0:0\n")
        self.assertEqual(self.evaluate("hibernate-stale-resume-device", m).status, engine.FIXED)
        m.write("/sys/power/resume", "259:2\n")
        self.assertEqual(self.evaluate("hibernate-stale-resume-device", m).status, engine.APPLIES)

    def test_a_failed_suspend_is_not_blamed_on_swap(self):
        m = self.machine()
        m.write("/proc/swaps", "Filename Type Size Used Priority\n")
        m.journal("systemd-sleep[812]: Failed to put system to sleep. System resumed again: Device or resource busy\n")
        self.assertFalse(self.evaluate("hibernate-not-enough-swap", m).found)
        m.journal("systemd-sleep[812]: Failed to put system to sleep. System resumed again: No space left on device\n")
        self.assertTrue(self.evaluate("hibernate-not-enough-swap", m).found)

    def test_hibernate_mode_set_in_the_main_config_counts(self):
        m = self.machine()
        m.write("/sys/power/disk", "[platform] shutdown reboot suspend\n")
        m.write("/etc/systemd/sleep.conf", "[Sleep]\nHibernateMode=shutdown\n")
        self.assertEqual(self.evaluate("hibernate-no-poweroff", m).status, engine.FIXED)


class EarlyoomTests(AuditCase):
    def test_a_cgroup_hitting_its_own_limit_is_not_memory_running_out(self):
        m = self.machine()
        m.journal("kernel: oom-kill:constraint=CONSTRAINT_MEMCG,nodemask=(null),cpuset=/,mems_allowed=0\n"
                  "kernel: Memory cgroup out of memory: Killed process 4242 (node) total-vm:1kB\n")
        self.assertFalse(self.evaluate("memory-freeze-earlyoom", m).found)
        m.journal("kernel: oom-kill:constraint=CONSTRAINT_NONE,nodemask=(null),cpuset=/,mems_allowed=0\n"
                  "kernel: Out of memory: Killed process 4242 (firefox) total-vm:1kB\n")
        self.assertTrue(self.evaluate("memory-freeze-earlyoom", m).found)


if __name__ == "__main__":
    unittest.main()
