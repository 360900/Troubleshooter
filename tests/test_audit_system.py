"""Audit of the system entries: disks, boot, updates, memory, CPU."""
import unittest

from tests.test_engine import DB, EngineCase
from troubleshooter import engine


class FstabTests(EngineCase):
    TIMEOUT = ("systemd[1]: dev-disk-by\\x2duuid-1234.device: Job dev-disk-by\\x2duuid-1234.device/start timed out.\n"
               "systemd[1]: Timed out waiting for device /dev/disk/by-uuid/1234.\n"
               "systemd[1]: Dependency failed for /mnt/data.\n")

    def fstab(self, journal):
        m = self.machine()
        m.write("/etc/fstab", "UUID=1234 /mnt/data ext4 defaults 0 2\n")
        m.journal(journal)
        return engine.evaluate(DB.entries["fstab-missing-device"], m.system())

    def test_a_nofail_drive_that_is_unplugged_is_not_recommended(self):
        # Startup doesn't wait for it, so this is just the advice already followed.
        self.assertFalse(self.fstab(self.TIMEOUT).recommended)

    def test_a_drive_that_held_up_startup_is_recommended(self):
        d = self.fstab(self.TIMEOUT + "systemd[1]: Dependency failed for Local File Systems.\n")
        self.assertTrue(d.recommended)
        self.assertTrue(self.fstab(self.TIMEOUT + "systemd[1]: Dependency failed for Swaps.\n").recommended)


if __name__ == "__main__":
    unittest.main()
