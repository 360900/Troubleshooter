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


if __name__ == "__main__":
    unittest.main()
