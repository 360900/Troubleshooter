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


if __name__ == "__main__":
    unittest.main()
