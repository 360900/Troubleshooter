import struct
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
