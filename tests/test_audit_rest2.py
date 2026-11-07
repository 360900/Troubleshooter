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


if __name__ == "__main__":
    unittest.main()
