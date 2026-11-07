import os
import struct
import time
import unittest

from tests.fakes import FakeMachine
from tests.test_boot import efi_option
from troubleshooter import engine
from troubleshooter.boot import EFI_GLOBAL
from troubleshooter.database import load_database
from troubleshooter.probes import probe
from troubleshooter.system import Result


class FailedCommandTests(unittest.TestCase):
    """A command that failed or timed out tells us nothing, so `absent` mustn't match on it."""

    def setUp(self):
        self.m = FakeMachine()
        self.addCleanup(self.m.close)

    def check(self, result, spec):
        self.m.outputs["commands"] = {("resolvectl", "mdns"): result}
        return probe(self.m.system(), {"source": "command", "argv": ["resolvectl", "mdns"], **spec})

    def test_error_output_is_unknown(self):
        failed = Result(1, "Failed to get global data: Unit dbus-org.freedesktop.resolve1.service not found.\n")
        evidence = self.check(failed, {"regex": "^Global: yes$", "absent": True})
        self.assertFalse(evidence.matched)
        self.assertFalse(evidence.known)

    def test_timeout_is_unknown(self):
        evidence = self.check(Result(124, ""), {"regex": "^Global: yes$", "absent": True})
        self.assertFalse(evidence.matched)
        self.assertFalse(evidence.known)

    def test_success_still_matches(self):
        self.assertTrue(self.check("Global: no\n", {"regex": "^Global: yes$", "absent": True}).matched)

    def test_expected_exit_code_is_still_checked(self):
        self.assertTrue(self.check(Result(1, "x\n"), {"exit": 1, "contains": "x"}).matched)
        self.assertFalse(self.check(Result(0, "x\n"), {"exit": 1, "contains": "x"}).matched)


class FailedJournalTests(unittest.TestCase):
    def test_missing_boot_is_unknown_not_empty(self):
        m = FakeMachine()
        self.addCleanup(m.close)
        system = m.system()
        system._commands = lambda argv: Result(1, "No journal boot entry found for the specified boot (-1).\n")
        evidence = probe(system, {"source": "journal", "boot": -1, "contains": "boot", "absent": True})
        self.assertFalse(evidence.matched)
        self.assertFalse(evidence.known)



class FileProbeTests(unittest.TestCase):
    def setUp(self):
        self.m = FakeMachine()
        self.addCleanup(self.m.close)

    def check(self, **spec):
        return probe(self.m.system(), {"source": "file", **spec})

    def test_nothing_found_under_a_root_only_directory_is_unknown(self):
        boot = self.m.root / "boot"
        boot.mkdir(mode=0o700)
        boot.chmod(0)
        self.addCleanup(boot.chmod, 0o700)
        if os.access(boot, os.R_OK):
            self.skipTest("running as root")
        for path in ("/boot/grub/grub.cfg", "/boot/*.conf"):
            with self.subTest(path=path):
                evidence = self.check(path=path, absent=True)
                self.assertFalse(evidence.known)
                self.assertFalse(evidence.matched)

    def test_nothing_found_in_a_readable_directory_is_still_absent(self):
        self.m.write("/etc/os-release", "ID=arch\n")
        self.assertTrue(self.check(path="/etc/nothing.conf", absent=True).matched)
        self.assertTrue(self.check(path="/etc/nothing/deeper.conf", absent=True).matched)

    def test_broken_links_are_found(self):
        # Relative, so the link stays inside the fake root.
        os.symlink("../run/systemd/resolve/stub-resolv.conf", self.m.root / "etc/resolv.conf")
        evidence = self.check(path="/etc/resolv.conf", broken_link=True)
        self.assertTrue(evidence.matched)
        self.assertIn("stub-resolv.conf", evidence.lines[0])
        self.m.write("/run/systemd/resolve/stub-resolv.conf", "nameserver 127.0.0.53\n")
        self.assertFalse(self.check(path="/etc/resolv.conf", broken_link=True).matched)

    def test_max_age_ignores_old_and_unstamped_lines(self):
        def stamp(days_ago):
            return time.strftime("[%Y-%m-%dT%H:%M:%S%z]", time.localtime(time.time() - days_ago * 86400))
        spec = {"path": "/var/log/pacman.log", "regex": "No space left", "max_age_days": 14}
        self.m.write("/var/log/pacman.log", f"{stamp(400)} [ALPM-SCRIPTLET] cp: No space left on device\n"
                                            "cp: No space left on device\n")
        self.assertFalse(self.check(**spec).matched)
        self.m.write("/var/log/pacman.log", f"{stamp(2)} [ALPM-SCRIPTLET] cp: No space left on device\n")
        self.assertTrue(self.check(**spec).matched)

    def test_user_placeholder_is_the_current_user(self):
        self.m.write("/etc/subuid", "someone:100000:65536\n")
        spec = {"path": "/etc/subuid", "regex": "^{user}:\\d+:\\d+"}
        self.assertFalse(self.check(**spec).matched)
        self.m.write("/etc/subuid", "someone:100000:65536\ntester:165536:65536\n")
        self.assertTrue(self.check(**spec).matched)


class PackageListTests(unittest.TestCase):
    def test_unreadable_package_list_does_not_make_packages_missing(self):
        m = FakeMachine()
        self.addCleanup(m.close)
        system = m.system()
        system._commands = lambda argv: Result(1, "error: could not open database\n")
        evidence = probe(system, {"source": "package", "names": ["amd-ucode"], "absent": True})
        self.assertFalse(evidence.matched)
        self.assertFalse(evidence.known)

    def test_readable_package_list_still_reports_missing_packages(self):
        m = FakeMachine()
        self.addCleanup(m.close)
        m.packages = {"linux"}
        self.assertTrue(probe(m.system(), {"source": "package", "names": ["amd-ucode"], "absent": True}).matched)


class BootLoaderTests(unittest.TestCase):
    def setUp(self):
        self.m = FakeMachine()
        self.addCleanup(self.m.close)

    def booted(self, description, path):
        efivars = self.m.root / "sys/firmware/efi/efivars"
        efivars.mkdir(parents=True)
        (efivars / f"BootCurrent-{EFI_GLOBAL}").write_bytes(struct.pack("<IH", 7, 3))
        (efivars / f"Boot0003-{EFI_GLOBAL}").write_bytes(efi_option(description, path))

    def test_names_the_loader_the_firmware_started(self):
        self.booted("GRUB", "\\EFI\\GRUB\\grubx64.efi")
        self.assertTrue(probe(self.m.system(), {"source": "boot_loader", "regex": "^grub$"}).matched)
        self.assertFalse(probe(self.m.system(), {"source": "boot_loader", "regex": "^limine$"}).matched)

    def test_unknown_without_firmware_variables(self):
        evidence = probe(self.m.system(), {"source": "boot_loader", "regex": "^grub$", "absent": True})
        self.assertFalse(evidence.matched)
        self.assertFalse(evidence.known)

    def test_grub_entries_apply_when_the_menu_is_on_a_root_only_esp(self):
        # /boot/grub/grub.cfg can't be seen, but the firmware says it started GRUB.
        self.m.packages = {"grub"}
        self.m.write("/etc/default/grub", "GRUB_TIMEOUT=5\n")
        self.booted("GRUB", "\\EFI\\GRUB\\grubx64.efi")
        entry = load_database().entries["grub-menu-hidden"]
        self.assertNotEqual(engine.evaluate(entry, self.m.system()).status, engine.NOT_APPLICABLE)


if __name__ == "__main__":
    unittest.main()
