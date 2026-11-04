import unittest

from tests.fakes import ARCH, CACHY, DEBIAN, FEDORA, NOBARA, UBUNTU, FakeMachine


class ScopeTests(unittest.TestCase):
    CASES = {
        ARCH: {"any", "arch-only", "arch-and-based"},
        CACHY: {"any", "arch-and-based"},
        DEBIAN: {"any", "debian-only", "debian-and-based"},
        UBUNTU: {"any", "debian-and-based"},
        FEDORA: {"any", "fedora-only", "fedora-and-based"},
        NOBARA: {"any", "fedora-and-based"},
    }

    def test_each_distro_gets_exactly_its_scopes(self):
        every = {"any", "arch-only", "arch-and-based", "debian-only", "debian-and-based", "fedora-only", "fedora-and-based"}
        for release, expected in self.CASES.items():
            machine = FakeMachine(release)
            self.addCleanup(machine.close)
            system = machine.system()
            with self.subTest(release=release):
                self.assertEqual({s for s in every if system.in_scope(s)}, expected)
