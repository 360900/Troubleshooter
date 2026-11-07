"""The app as numbered menus in a terminal, for when the window can't start or there's no desktop.

It never imports Qt, so it works on a bare TTY or over SSH. The flow and wording follow the window.
"""
from __future__ import annotations

import argparse
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import textwrap
import time
from pathlib import Path

from . import engine, history
from .database import Category, Database, load_database
from .patches import PlanError
from .search import STRONG_MATCH, SearchIndex
from .system import SCOPES, System

ADVANCED_WARNING = "ADVANCED! Check this box ONLY if you know exactly what you are doing!"
TONES = {"success": "32", "warning": "33", "danger": "31", "accent": "36", "dim": "2", "strong": "1"}
LEVEL_TONE = {"High": "success", "Medium": "warning", "Low": "dim"}
MAX_WIDTH = 100


class Quit(Exception):
    """Leaves the app from any screen."""


class GoHome(Exception):
    """Back to the start, after a fix or an undo has run."""


def default_elevate() -> list[str]:
    if os.geteuid() == 0:
        return []
    for tool in ("sudo", "pkexec"):
        if shutil.which(tool):
            return [tool]
    return ["sudo"]


def _scopes(scopes: list[str]) -> str:
    return " and ".join(SCOPES[s] for s in scopes)


class Session:
    def __init__(self, db: Database, make_system=System, hist: history.History | None = None,
                 dismissed: history.Dismissed | None = None, elevate: list[str] | None = None,
                 ask=input, out=None, editor: str | None = None, color: bool | None = None):
        self.db = db
        self.make_system = make_system
        self.history = hist or history.History()
        self.dismissed = dismissed or history.Dismissed()
        self.elevate = elevate if elevate is not None else default_elevate()
        self.ask = ask
        self.out = out or sys.stdout
        self.editor = editor
        self.color = color if color is not None else (
            self.out.isatty() and "NO_COLOR" not in os.environ and os.environ.get("TERM") != "dumb")
        encoding = (getattr(self.out, "encoding", None) or "").lower().replace("-", "")
        self.bar = ("█", "░") if encoding == "utf8" else ("#", "-")
        self.reboot_command = ["systemctl", "reboot"]
        self.index = SearchIndex(db)
        self.system = make_system()
        self.found: list[engine.Diagnosis] | None = None  # the launch check, dismissed ones included
        self.pending: list[history.Record] = []

    # Output

    @property
    def width(self) -> int:
        return min(shutil.get_terminal_size((80, 24)).columns, MAX_WIDTH)

    def paint(self, text: str, tone: str | None) -> str:
        if not self.color or not tone:
            return text
        return f"\033[{TONES[tone]}m{text}\033[0m"

    def write(self, text: str = "") -> None:
        self.out.write(text + "\n")
        self.out.flush()

    def say(self, text: str = "", tone: str | None = None, indent: int = 0, first: str | None = None) -> None:
        if not text:
            self.write()
            return
        lead = " " * indent
        for line in textwrap.wrap(text, self.width - 1, initial_indent=first if first is not None else lead,
                                  subsequent_indent=lead) or [""]:
            self.write(self.paint(line, tone))

    def heading(self, text: str) -> None:
        self.write()
        self.say(text, "strong")

    def section(self, text: str, note: str = "") -> None:
        self.write()
        self.say(text, "strong")
        if note:
            self.say(note, "dim")

    def item(self, key: str | int, title: str, subtitle: str = "", pill: engine.Diagnosis | None = None,
             tone: str | None = None) -> None:
        """A list row. A pill is a confidence shown after the title, kept out of the wrapping."""
        bullet = f"  {key}) " if key != "" else "  - "
        tag = f"{pill.confidence}%" if pill else ""
        lines = textwrap.wrap(title, self.width - 1 - len(tag) - 2, initial_indent=bullet,
                              subsequent_indent=" " * len(bullet))
        for line in lines[:-1]:
            self.write(self.paint(line, tone))
        self.write(self.paint(lines[-1], tone) + (f"  {self.paint(tag, LEVEL_TONE[pill.level])}" if pill else ""))
        if subtitle:
            self.say(subtitle, "dim", len(bullet))

    def menu(self, *options: tuple[str, str]) -> None:
        """One line of choices, wrapped by their plain width so color codes don't count."""
        self.write()
        line, width = [], 2
        for key, text in options:
            if not key:
                continue
            size = len(key) + 2 + len(text)
            if line and width + 3 + size > self.width - 1:
                self.write("  " + "   ".join(line))
                line, width = [], 2
            line.append(f"{self.paint(key + ')', 'accent')} {text}")
            width += size + (3 if len(line) > 1 else 0)
        self.write("  " + "   ".join(line))

    # Input

    def choose(self, prompt: str = "> ") -> str:
        try:
            answer = self.ask(self.paint(prompt, "accent")).strip()
        except EOFError:
            self.write()
            raise Quit from None
        if answer.lower() == "q":
            raise Quit
        return answer

    def confirm(self, question: str) -> bool:
        return self.choose(f"{question} [y/N] ").lower() in ("y", "yes")

    def pick(self, answer: str, count: int) -> int | None:
        """The 0-based index of a numbered choice, or None."""
        if answer.isdigit() and 1 <= int(answer) <= count:
            return int(answer) - 1
        return None

    def unknown(self) -> bool:
        """Says so. Returns True, for screens to skip redrawing before asking again."""
        self.say("Pick one of the options above.", "warning")
        return True

    # Running

    def run(self) -> int:
        try:
            self.home()
        except Quit:
            return 0
        except KeyboardInterrupt:
            self.write()
            return 130
        return 0

    def fresh_system(self, cursor: str | None = None) -> System:
        system = self.make_system()
        system.cursor = cursor
        return system

    # Home

    def scan(self) -> None:
        self.write()
        self.say(f"Checking this PC… Looking at {self.system.os_name}'s hardware, drivers and logs. Nothing is changed.",
                 "dim")
        system = self.make_system()
        self.pending = self.history.pending_restart(system.boot_time)
        waiting = {r.entry_id for r in self.pending}
        self.found = [d for d in engine.scan(self.db, system) if d.entry.id not in waiting]

    def split_found(self) -> tuple[list[engine.Diagnosis], list[engine.Diagnosis]]:
        hidden = self.dismissed.ids()
        return ([d for d in self.found if d.entry.id not in hidden],
                [d for d in self.found if d.entry.id in hidden])

    def home(self) -> None:
        quiet = False
        while True:
            if self.found is None:
                self.scan()
            shown, hidden = self.split_found()
            if not quiet:
                self.show_home(shown, hidden)
            self.menu(("1-" + str(len(shown)) if len(shown) > 1 else "1" if shown else "", "Open"),
                      ("s", "Search"), ("c", "Categories"), ("h", "Applied fixes"),
                      ("d" if hidden else "", "Dismissed"), ("r", "Check again"), ("q", "Quit"))
            try:
                quiet = self.home_choice(self.choose("Describe the problem, or pick an option: "), shown, hidden)
            except GoHome:
                quiet = False

    def show_home(self, shown: list[engine.Diagnosis], hidden: list[engine.Diagnosis]) -> None:
        self.heading(f"Troubleshooter · {self.system.os_name}")
        if self.pending:
            self.section("Waiting for a restart")
            for record in self.pending:
                self.item("", record.title, "Applied. It takes effect after you restart.", tone="accent")
        if not shown:
            self.section("On this PC")
            self.say("No known problems found.", "success", 2)
            self.say(f"Everything we check automatically looks fine on {self.system.os_name}.", "dim", 2)
        else:
            count = len(shown)
            self.section("Found on this PC",
                         f"{count} recommended fix{'es' if count > 1 else ''}. Nothing is changed until you say so.")
            for number, d in enumerate(shown, 1):
                why = d.found[0].signal.says if d.found else d.entry.summary
                self.item(number, d.entry.title, why, d)
        if hidden:
            self.section("Dismissed", f"{len(hidden)} still found on this PC, but not recommended. Nothing was changed.")

    def home_choice(self, answer: str, shown: list[engine.Diagnosis], hidden: list[engine.Diagnosis]) -> bool:
        """Acts on the answer. Returns True when the screen needn't be drawn again."""
        key = answer.lower()
        index = self.pick(answer, len(shown))
        if not answer:
            return True
        if index is not None:
            self.fix_screen(shown[index])
        elif key == "s":
            text = self.choose("Describe the problem: ")
            if text:
                self.search(text)
        elif key == "c":
            self.categories()
        elif key == "h":
            self.history_screen()
        elif key == "d" and hidden:
            self.dismissed_screen()
        elif key == "r":
            self.found = None
        elif len(answer) > 1 and not answer.isdigit():
            self.search(answer)
        else:
            return self.unknown()
        return False

    def dismissed_screen(self) -> None:
        quiet = False
        while True:
            hidden = self.split_found()[1]
            if not hidden:
                return
            if not quiet:
                self.heading("Dismissed")
                self.say("Still found on this PC, but not recommended. Nothing was changed.", "dim")
                self.write()
                for number, d in enumerate(hidden, 1):
                    self.item(number, d.entry.title, d.entry.summary)
            quiet = False
            self.menu(("1-" + str(len(hidden)) if len(hidden) > 1 else "1", "Show again"), ("b", "Back"), ("q", "Quit"))
            answer = self.choose()
            index = self.pick(answer, len(hidden))
            if answer.lower() == "b":
                return
            if index is None:
                quiet = self.unknown()
                continue
            self.dismissed.remove(hidden[index].entry.id)

    # Finding the problem

    def search(self, text: str) -> None:
        hits = self.index.search(text, 12)
        hits = [h for h in hits if h.score >= hits[0].score * STRONG_MATCH] if hits else []
        if not hits:
            self.write()
            self.say("Nothing matches that yet", "strong")
            self.say("Try other words, or pick the closest category.", "dim")
            return
        entries = [h.target for h in hits if not h.is_category][:6]
        categories = [h.target for h in hits if h.is_category][:4]
        choices = [*entries, *categories]
        quiet = False
        while True:
            if not quiet:
                self.heading(f"Results for “{text}”")
                if entries:
                    self.section("Closest matches", "Pick one if it fits. If not, Check my PC looks through all of these.")
                    for number, entry in enumerate(entries, 1):
                        self.item(number, entry.title, entry.summary)
                if categories:
                    self.section("Categories")
                    for number, category in enumerate(categories, len(entries) + 1):
                        self.item(number, category.title)
            quiet = False
            self.menu((f"1-{len(choices)}" if len(choices) > 1 else "1", "Open"),
                      ("k" if entries else "", "Check my PC"), ("b", "Back"), ("q", "Quit"))
            answer = self.choose()
            index = self.pick(answer, len(choices))
            if answer.lower() == "b":
                return
            if answer.lower() == "k" and entries:
                every = [(h.target, h.score) for h in self.index.search(text, 12) if not h.is_category]
                self.results(f"Results for “{text}”", lambda s: engine.diagnose_hits(every, s))
            elif index is not None and index < len(entries):
                self.check_entry(entries[index])
            elif index is not None:
                self.category_screen(categories[index - len(entries)])
            else:
                quiet = self.unknown()

    def categories(self) -> None:
        cats = list(self.db.categories.values())
        quiet = False
        while True:
            if not quiet:
                self.heading("Categories")
                self.write()
                for number, category in enumerate(cats, 1):
                    self.item(number, category.title)
            quiet = False
            self.menu((f"1-{len(cats)}", "Open"), ("b", "Back"), ("q", "Quit"))
            answer = self.choose()
            index = self.pick(answer, len(cats))
            if answer.lower() == "b":
                return
            if index is None:
                quiet = self.unknown()
                continue
            self.category_screen(cats[index])

    def category_screen(self, category: Category) -> None:
        entries = sorted(self.db.in_category(category.id), key=lambda e: not self.system.in_scopes(e.systems))
        quiet = False
        while True:
            if not quiet:
                self.heading(category.title)
                self.say(category.blurb)
                self.section("Or pick a specific problem", "Not sure? Checking the whole category works fine.")
                for number, entry in enumerate(entries, 1):
                    fits = self.system.in_scopes(entry.systems)
                    self.item(number, entry.title, entry.summary if fits else "For " + _scopes(entry.systems))
            quiet = False
            self.menu(("k", "Check my PC"), (f"1-{len(entries)}" if len(entries) > 1 else "1", "Open"),
                      ("b", "Back"), ("q", "Quit"))
            answer = self.choose()
            index = self.pick(answer, len(entries))
            if answer.lower() == "b":
                return
            if answer.lower() == "k":
                self.results(f"{category.title} check", lambda s: engine.diagnose_category(self.db, category.id, s))
            elif index is not None:
                self.check_entry(entries[index])
            else:
                quiet = self.unknown()

    def results(self, heading: str, run) -> None:
        cursor = None
        while True:
            self.write()
            self.say("Reading your logs and hardware…", "dim")
            results = run(self.fresh_system(cursor))
            reproduced = cursor is not None
            offer = engine.can_reproduce(results) and not reproduced
            cursor = None
            live = [d for d in results if d.status == engine.APPLIES]
            likely = [d for d in live if d.recommended and d.found]
            rest = [d for d in live if d not in likely][:8]
            fine = [d for d in results if d.status == engine.FIXED]
            choices = [*likely, *rest]
            quiet = False
            while cursor is None:
                if not quiet:
                    self.show_results(heading, likely, rest, fine, offer, reproduced)
                quiet = False
                self.menu((f"1-{len(choices)}" if len(choices) > 1 else "1" if choices else "", "Open"),
                          ("p" if offer else "", "Show us the problem"), ("b", "Back"), ("q", "Quit"))
                answer = self.choose()
                index = self.pick(answer, len(choices))
                if answer.lower() == "b":
                    return
                if answer.lower() == "p" and offer:
                    cursor = self.reproduce("Do whatever makes the problem show up.")
                elif index is not None:
                    self.fix_screen(choices[index])
                else:
                    quiet = self.unknown()

    def show_results(self, heading, likely, rest, fine, offer, reproduced) -> None:
        self.heading(heading)
        if reproduced and not likely:
            self.say("Nothing new showed up in the logs while you reproduced the problem.")
        if likely:
            self.say(f"{len(likely)} likely cause{'s' if len(likely) > 1 else ''}. Pick one to see what would change.")
            self.write()
            for number, d in enumerate(likely, 1):
                self.item(number, d.entry.title, d.found[0].signal.says, d, "warning")
        elif not reproduced:
            self.say("We didn't find anything conclusive in your logs.")
        if offer:
            self.section("Show us the problem",
                         "Make it happen once while we watch the logs. This usually settles it. Press p to start.")
        if rest:
            self.section("Less likely", "Nothing on your PC points to these, but they could still be it.")
            for number, d in enumerate(rest, len(likely) + 1):
                self.item(number, d.entry.title, d.entry.summary, d)
        if fine:
            self.section("Ruled out", "We checked these, and they aren't the problem here.")
            for d in fine:
                self.item("", d.entry.title, tone="success")
        if not likely and not rest and not fine:
            self.write()
            self.say("None of these apply to this PC", "success")
            self.say("This hardware or setup isn't affected by the problems we know about here.", "dim")

    def reproduce(self, prompt: str) -> str | None:
        """Waits while the user makes the problem happen. Returns where the logs stood before, or None."""
        cursor = self.make_system().journal_cursor()
        if cursor is None:
            self.write()
            self.say("Couldn't read the system logs, so we can't watch for the problem.", "warning")
            return None
        self.heading("Make it happen now")
        self.say(prompt)
        self.write()
        self.say("Watching the system logs… Come back here when it has happened, even if it only happened once.",
                 "dim")
        self.menu(("Enter", "It happened"), ("b", "Cancel"), ("q", "Quit"))
        while True:
            answer = self.choose()
            if not answer:
                return cursor
            if answer.lower() == "b":
                return None
            self.unknown()

    # The fix

    def check_entry(self, entry) -> None:
        self.write()
        self.say("Reading your logs and hardware…", "dim")
        self.fix_screen(engine.diagnose_entry(entry, self.fresh_system()))

    def plan_for(self, d: engine.Diagnosis):
        try:
            return engine.build_plan(d, self.make_system()), None
        except PlanError as err:
            return None, str(err)

    def fix_screen(self, d: engine.Diagnosis) -> None:
        plan, error = self.plan_for(d)
        script = plan.script if plan else ""
        quiet = False
        while True:
            entry = d.entry
            edited = bool(plan) and script != plan.script
            dismissed = entry.id in self.dismissed.ids()
            # Only what the launch check would bring up can be dismissed from it.
            can_dismiss = dismissed or (entry.auto_detect and d.recommended)
            can_reproduce = bool(d.status == engine.APPLIES and entry.reproduce
                                 and any(s.spec.get("source") == "journal" for s in entry.signals)
                                 and not any(f.evidence.in_window for f in d.found) and not d.recommended)
            can_apply = bool(d.status == engine.APPLIES and plan and not (entry.advice and not entry.fixes))
            if not quiet:
                self.show_fix(d, plan, error, can_reproduce)
            quiet = False
            self.menu(("a" if can_apply else "", "Apply edited patch" if edited else "Apply fix"),
                      ("v" if can_apply else "", "View script"), ("e" if can_apply else "", "Edit script"),
                      ("o" if edited else "", "Reset to the original patch"),
                      ("p" if can_reproduce else "", "Show us the problem"),
                      ("d" if can_dismiss else "", "Recommend this again" if dismissed else "Don't recommend this again"),
                      ("b", "Back"), ("q", "Quit"))
            key = self.choose().lower()
            if key == "b":
                return
            if key == "a" and can_apply:
                if edited:
                    self.say("This runs as root exactly as written. Undo only reverses the original patch's changes, "
                             "so edits you add may stay.", "danger")
                if self.confirm("Apply edited patch now?" if edited else "Apply this fix now?"):
                    self.apply(plan, script, edited)
                quiet = True
            elif key == "v" and can_apply:
                self.write()
                for line in script.splitlines():
                    self.write(f"  {line}")
                quiet = True
            elif key == "e" and can_apply:
                self.write()
                self.say(ADVANCED_WARNING, "danger")
                if self.confirm("Open the script in a text editor?"):
                    changed = self.edit(script)
                    if changed is not None and not changed.strip():
                        self.say("The script is empty, so nothing was changed.", "warning")
                    elif changed is not None:
                        script = changed
                        if script != plan.script:
                            self.say("Apply now runs your edited version.", "danger")
                quiet = True
            elif key == "o" and edited:
                script = plan.script
                self.say("Back to the original patch.")
                quiet = True
            elif key == "p" and can_reproduce:
                cursor = self.reproduce(entry.reproduce)
                if cursor is not None:
                    self.write()
                    self.say("Reading your logs and hardware…", "dim")
                    d = engine.diagnose_entry(entry, self.fresh_system(cursor))
                    plan, error = self.plan_for(d)
                    script = plan.script if plan else ""
            elif key == "d" and can_dismiss:
                (self.dismissed.remove if dismissed else self.dismissed.add)(entry.id)
                self.say("It's recommended on launch again." if dismissed else
                         "It won't be recommended on launch anymore. Nothing on your PC was changed.")
                quiet = True
            else:
                quiet = self.unknown()

    def show_fix(self, d: engine.Diagnosis, plan, error: str | None, can_reproduce: bool) -> None:
        entry = d.entry
        os_name = self.system.os_name
        self.heading(entry.title)
        self.say(entry.summary)

        def box(title, text, tone):
            self.write()
            self.say(title, tone)
            self.say(text, "dim")

        def learn_more():
            if entry.source:
                self.section("Learn more")
                self.write("  " + self.paint(entry.source, "accent"))  # whole, so it stays clickable

        if d.status == engine.WRONG_SYSTEM:
            box("This isn't meant for your system",
                f"It's written for {_scopes(entry.systems)}. You're on {os_name}, so nothing can be applied.", "warning")
            learn_more()
            return
        if d.status == engine.NOT_APPLICABLE:
            box("This doesn't apply to this PC", "Your hardware or setup isn't affected by this problem.", "success")
            learn_more()
            return
        if d.status == engine.FIXED:
            box("This is already taken care of", "The fix is in place, or isn't needed on this setup.", "success")
            learn_more()
            return

        words = {"High": "High confidence this is your problem",
                 "Medium": "Medium confidence this is your problem",
                 "Low": "Low confidence: little on your PC confirms this"}[d.level]
        full, empty = self.bar
        filled = round(d.confidence / 5)
        self.write()
        self.write(f"  {self.paint(str(d.confidence) + '%', 'strong')}  {self.paint(words, 'strong')}")
        self.write("  " + self.paint(full * filled, LEVEL_TONE[d.level]) + self.paint(empty * (20 - filled), "dim"))

        self.section("What we found")
        if not d.found:
            self.item("", "Nothing on your PC confirms this yet", "It's listed because it matches what you described.")
        for finding in d.found:
            note = "Appeared while you reproduced the problem" if finding.evidence.in_window else ""
            self.item("", finding.signal.says, note, tone="accent")
            for line in finding.evidence.lines[:2]:
                self.write("      " + self.paint(line, "dim"))
        if d.unread:
            self.say(f"{d.unread} check{'s' if d.unread > 1 else ''} couldn't run here, "
                     "for example because a log isn't readable.", "dim")

        if can_reproduce:
            self.section("Show us the problem", entry.reproduce)

        if entry.advice and not entry.fixes:
            self.section("What to do", "We don't change anything automatically for this one.")
            self.say(entry.advice, indent=2)
        elif error:
            box("Nothing to change", error, "success")
        elif not plan and entry.advice:
            self.section("What to do", f"There's no automatic fix for {os_name}.")
            self.say(entry.advice, indent=2)
        elif not plan:
            box("No fix for your system yet", f"Fixes exist for {_scopes(entry.systems)}. You're on {os_name}.",
                "warning")
        else:
            self.section("What will change", f"Written for {_scopes(d.fix.systems)}. You're on {os_name}.")
            for number, says in enumerate(plan.summary, 1):
                self.item(number, says)
            self.write()
            self.say("Files are backed up first, and you can undo this from Applied fixes.", indent=2)
            if plan.reboot:
                self.say("You'll need to restart afterwards.", indent=2)
            if entry.advice:
                self.say(entry.advice, indent=2)
            if not d.recommended:
                self.write()
                self.say("Confidence is low. This may not be your problem, so only apply it if the description fits.",
                         "warning")
        learn_more()

    def edit(self, script: str) -> str | None:
        """The script after the user edited it, or None when no editor could run."""
        editor = self.editor or os.environ.get("VISUAL") or os.environ.get("EDITOR") or next(
            (e for e in ("nano", "vim", "vi") if shutil.which(e)), None)
        if not editor:
            self.say("No text editor found. Set $EDITOR and try again.", "danger")
            return None
        with tempfile.TemporaryDirectory(prefix="troubleshooter-") as tmp:
            path = Path(tmp) / "fix.sh"
            path.write_text(script)
            try:
                code = subprocess.run([*shlex.split(editor), str(path)]).returncode
            except OSError as err:
                self.say(f"Couldn't start {editor}: {err.strerror}", "danger")
                return None
            if code != 0:
                self.say(f"{editor} exited with an error, so nothing was changed.", "warning")
                return None
            return path.read_text()

    # Applying

    def run_root(self, script: str) -> str:
        """Runs a script as root, showing its output. Returns ok, failed, cancelled or start_failed."""
        argv = [*self.elevate, "/bin/bash", "-c", script]
        # In its own process group, Ctrl+C reaches only us, so the script can't be killed halfway.
        # Only possible when no password prompt needs the terminal while it runs.
        detach = not self.elevate
        if self.elevate and os.path.basename(self.elevate[0]) == "sudo":
            self.say("Waiting for your password…", "dim")
            try:
                if subprocess.run([self.elevate[0], "-v"]).returncode != 0:
                    return "cancelled"
                # Checked the way the script will run, in case sudo remembers the password per process group.
                cached = subprocess.run([self.elevate[0], "-n", "true"], stdin=subprocess.DEVNULL,
                                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                        process_group=0).returncode == 0
            except OSError:
                return "start_failed"
            if cached:
                argv = [self.elevate[0], "-n", *argv[1:]]
                detach = True
        try:
            # Script goes in as an argument, not on stdin, as in the window.
            process = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                       stderr=subprocess.STDOUT, process_group=0 if detach else None)
        except OSError:
            return "start_failed"
        saw_output = False
        while True:
            try:
                for raw in iter(process.stdout.readline, b""):
                    line = raw.decode(errors="replace").rstrip("\n")
                    saw_output = saw_output or "==> " in line
                    self.write(self.paint(line, "strong") if line.startswith("==> ") else line)
                code = process.wait()
                break
            except KeyboardInterrupt:
                self.say("Still working. Please wait for this to finish.", "warning")
        process.stdout.close()
        # pkexec exits 126 when the prompt is dismissed and 127 when refused, before the script runs.
        if code in (126, 127) and not saw_output and self.elevate:
            return "cancelled"
        return "ok" if code == 0 else "failed"

    def report(self, outcome: str) -> None:
        if outcome == "cancelled":
            self.say("Cancelled. Nothing was changed.")
        elif outcome == "start_failed":
            self.say(f"Couldn't ask for permission to make changes. Is {self.elevate[0]} installed?", "danger")

    def apply(self, plan, script: str, edited: bool) -> None:
        self.heading(plan.title)
        outcome = self.run_root(script)
        record = None
        if outcome == "ok":
            record = self.history.add(plan, history.APPLIED, edited)
            self.found = None
            self.write()
            self.say("Done. Restart your PC to finish." if plan.reboot else "Done.", "success")
        elif outcome == "failed":
            record = self.history.add(plan, history.FAILED, edited)
            self.write()
            self.say("This fix failed partway. Undo restores what it changed.", "danger")
        else:
            self.report(outcome)
            return
        self.finish(record, plan.reboot and outcome == "ok")

    def undo(self, record: history.Record) -> None:
        self.heading(f"Undoing: {record.title}")
        if record.edited:
            self.say("You edited this patch, so undo only reverses the original patch's changes.", "warning")
        outcome = self.run_root(record.undo_script)
        if outcome == "ok":
            self.history.mark(record.run_id, history.UNDONE)
            self.found = None
            self.write()
            self.say("Undone. Restart your PC to finish." if record.reboot else "Undone.", "success")
            self.finish(None, record.reboot)
        elif outcome == "failed":
            self.write()
            self.say("Undo finished with errors. Check the output above.", "danger")
            self.finish(record, False)
        else:
            self.report(outcome)

    def finish(self, record: history.Record | None, can_reboot: bool) -> None:
        """What to do after a run. Leads home, like the window does."""
        while True:
            self.menu(("u" if record else "", "Undo"), ("r" if can_reboot else "", "Restart now"),
                      ("Enter", "Done"), ("q", "Quit"))
            key = self.choose().lower()
            if not key:
                raise GoHome
            if key == "u" and record:
                self.undo(record)
                raise GoHome
            if key == "r" and can_reboot:
                if self.confirm("Restart now?"):
                    subprocess.Popen(self.reboot_command)
                    raise Quit
            else:
                self.unknown()

    def history_screen(self) -> None:
        quiet = False
        while True:
            records = self.history.load()
            if not quiet:
                self.heading("Applied fixes")
                self.say("Everything Troubleshooter changed on this PC.")
                self.write()
                if not records:
                    self.say("Nothing yet", "strong")
                    self.say("Fixes you apply show up here, and you can undo them.", "dim")
                for number, record in enumerate(records, 1):
                    when = time.strftime("%d %b %Y, %H:%M", time.localtime(record.time))
                    state = {history.APPLIED: "Applied", history.FAILED: "Failed partway",
                             history.UNDONE: "Undone"}[record.status]
                    self.item(number, record.title, f"{state} on {when}" + (". Edited by you" if record.edited else ""))
            quiet = False
            self.menu((f"1-{len(records)}" if len(records) > 1 else "1" if records else "", "Undo"),
                      ("b", "Back"), ("q", "Quit"))
            answer = self.choose()
            index = self.pick(answer, len(records))
            if answer.lower() == "b":
                return
            if index is None:
                quiet = self.unknown()
            elif records[index].status == history.UNDONE:
                self.say("That one is already undone.", "warning")
                quiet = True
            elif self.confirm(f"Undo “{records[index].title}”?"):
                self.undo(records[index])
            else:
                quiet = True

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="troubleshooter-cli",
        description="Finds and fixes common Linux problems, in the terminal. For scripts, use "
                    "troubleshooter scan, search, diagnose or plan.")
    parser.parse_args(sys.argv[1:] if argv is None else argv)
    if not sys.stdin.isatty():
        print("troubleshooter-cli needs a terminal. For scripts, use troubleshooter scan, search, diagnose or plan.",
              file=sys.stderr)
        return 2
    return Session(load_database()).run()


if __name__ == "__main__":
    sys.exit(main())
