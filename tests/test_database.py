import unittest

from troubleshooter.database import DatabaseError, load_database, parse_entry
from troubleshooter.system import SCOPES


class DatabaseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db = load_database()

    def test_every_entry_says_which_systems_it_is_for(self):
        for entry in self.db.entries.values():
            self.assertTrue(entry.systems, entry.id)
            self.assertTrue(set(entry.systems) <= set(SCOPES), entry.id)
            for fix in entry.fixes:
                self.assertTrue(fix.systems, entry.id)

    def test_every_category_has_entries(self):
        for category in self.db.categories:
            self.assertTrue(self.db.in_category(category), category)

    def test_mbp132_patches_are_not_in_the_database_yet(self):
        for entry in self.db.entries.values():
            self.assertNotIn("mbp132", entry.id)

    def test_probes_only_run_read_only_commands(self):
        def commands(spec):
            for group in ("all_of", "any_of"):
                for part in spec.get(group, []):
                    yield from commands(part)
            if spec.get("source") == "command":
                yield spec["argv"]
        for entry in self.db.entries.values():
            specs = [*entry.when, *entry.fixed_when, *(s.spec for s in entry.signals)]
            for spec in specs:
                self.assertEqual(list(commands(spec)), [], f"{entry.id}: review new command probes for safety")

    def _entry(self, **extra):
        base = {"id": "x", "title": "x", "summary": "x", "categories": ["display"], "systems": ["any"], "advice": "x"}
        return parse_entry({**base, **extra}, "test", self.db.categories)

    def test_rejects_bad_entries(self):
        bad = [
            {"typo_key": 1},
            {"systems": ["ubuntu"]},
            {"systems": []},
            {"categories": ["nope"]},
            {"when": [{"source": "nope"}]},
            {"when": [{"source": "file"}]},
            {"when": [{"source": "file", "path": "/x", "regx": "y"}]},
            {"signals": [{"says": "x", "weight": 1.5, "source": "cmdline"}]},
            {"signals": [{"says": "x", "weight": 0.5, "source": "file", "path": "/x", "regex": "("}]},
            {"fix": [{"steps": [{"op": "nope"}]}]},
            {"fix": [{"steps": [{"op": "kernel_params", "add": ["a; rm -rf /"]}]}]},
            {"fix": [{"steps": [{"op": "install", "packages": ["a;b"]}]}]},
            {"fix": [{"steps": []}]},
            {"source": "wiki.archlinux.org/title/GRUB"},
            {"source": "http://wiki.archlinux.org/title/GRUB"},
        ]
        for extra in bad:
            with self.subTest(extra=extra), self.assertRaises(DatabaseError):
                self._entry(**extra)

    def test_needs_fix_or_advice(self):
        with self.assertRaises(DatabaseError):
            parse_entry({"id": "x", "title": "x", "summary": "x", "categories": ["display"], "systems": ["any"]},
                        "test", self.db.categories)
