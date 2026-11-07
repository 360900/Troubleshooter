import os
import unittest

from tests.test_engine import DB, EngineCase
from troubleshooter import engine


class DesktopCase(EngineCase):
    def evaluate(self, entry_id, m, env=None):
        system = m.system()
        system.env = env or {}
        return engine.evaluate(DB.entries[entry_id], system)


class JavaTests(DesktopCase):
    def java(self, default_target):
        m = self.machine()
        m.packages = {"java-runtime-common", "jre-openjdk"}
        m.write("/usr/lib/jvm/java-25-openjdk/bin/java", "")
        for link in ("default", "default-runtime"):
            os.symlink(default_target, m.root / "usr/lib/jvm" / link)
        return self.evaluate("java-default-broken", m)

    def test_links_left_on_dev_null_are_found(self):
        # Removing the default Java leaves both links pointing at /dev/null, as packaged.
        self.assertTrue(self.java("/dev/null").recommended)

    def test_working_links_are_fixed(self):
        self.assertEqual(self.java("java-25-openjdk").status, engine.FIXED)


if __name__ == "__main__":
    unittest.main()
