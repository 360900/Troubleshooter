import unittest

from tests.fakes import FakeMachine
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


if __name__ == "__main__":
    unittest.main()
