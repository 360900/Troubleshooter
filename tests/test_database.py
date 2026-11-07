import re
import unittest
from pathlib import Path

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

    def test_mbp132_setup_fixes_each_install_and_undo_one_part(self):
        parts = {"audio", "touchbar", "sleep", "wifi", "grub", "keyboard"}
        steps = [step for entry in self.db.entries.values() for fix in entry.fixes for step in fix.steps
                 if step["op"] == "run_from_repo" and step["repo"].endswith("/mbp132-setup")]
        self.assertGreaterEqual(len(steps), 9)
        # One pinned version for all of them, so the parts always come from the same tree.
        self.assertEqual({(s["commit"], s["script"]) for s in steps}, {(steps[0]["commit"], "mbp132-setup-cli.sh")})
        for step in steps:
            with self.subTest(says=step["says"]):
                kept = parts - {a.removeprefix("--no-") for a in step["args"] if a.startswith("--no-")}
                self.assertLessEqual(len(kept), 1)
                if "undo_args" in step:
                    self.assertEqual(step["undo_args"][0], "--uninstall")
                    self.assertEqual(parts - {a.removeprefix("--no-") for a in step["undo_args"][1:]}, kept)

    def test_probes_only_run_read_only_commands(self):
        reviewed = [
            ["resolvectl", "mdns"],  # status only, no arguments that change anything
            ["pacman", "-Qq", "pulse-native-provider"],  # local database query only
            ["locale", "-a"],  # lists the generated locales, changes nothing
            ["df", "--output=pcent", "/var/cache/pacman/pkg"],  # prints how full a disk is
            ["modinfo", "-F", "name", "loop"],  # reads module metadata from disk, loads nothing
        ]

        def commands(spec):
            for group in ("all_of", "any_of"):
                for part in spec.get(group, []):
                    yield from commands(part)
            if spec.get("source") == "command":
                yield spec["argv"]
        for entry in self.db.entries.values():
            specs = [*entry.when, *entry.fixed_when, *(s.spec for s in entry.signals)]
            for spec in specs:
                for argv in commands(spec):
                    self.assertIn(argv, reviewed, f"{entry.id}: review new command probes for safety")

    def test_entries_are_toml_1_0(self):
        """Python 3.11 to 3.13 only read TOML 1.0: no inline tables over several lines, no
        trailing commas in them, no \\x or \\e escapes in basic strings."""
        multiline = re.compile(r"'''.*?'''|\"\"\".*?\"\"\"", re.S)
        basic = r'"(?:[^"\\]|\\.)*"'
        for path in sorted((Path(__file__).parents[1] / "troubleshooter/data/entries").glob("*.toml")):
            for number, line in enumerate(multiline.sub("", path.read_text()).splitlines(), 1):
                where = f"{path.name}:{number}"
                for string in re.findall(basic, line):
                    self.assertNotRegex(string, r"(?<!\\)(\\\\)*\\[xe]", where)
                code = re.sub(rf"'[^']*'|{basic}", "", line)
                self.assertEqual(code.count("{"), code.count("}"), where)
                self.assertNotRegex(code, r",\s*}", where)

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
