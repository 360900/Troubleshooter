"""The screens. Pages show things and report what the user chose; window.py wires the flow."""
from __future__ import annotations

import time
from urllib.parse import unquote, urlsplit

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLineEdit, QPlainTextEdit, QVBoxLayout, QWidget

from .. import engine, history
from ..database import Category, Entry
from ..search import STRONG_MATCH
from ..system import SCOPES
from .widgets import (BoxedList, CheckBox, Meter, Page, Row, Spinner, button, clear_layout, hbox, label, number_tile, pill,
                      repolish)

ADVANCED_WARNING = "ADVANCED! Check this box ONLY if you know exactly what you are doing!"
LEVEL_ROLE = {"High": "success", "Medium": "warning", "Low": "text3"}


def confidence_pill(d: engine.Diagnosis):
    return pill(f"{d.confidence}%", d.level.lower())


def waiting(page: Page, text: str) -> None:
    page.reset()
    page.body.addSpacing(48)
    page.body.addLayout(hbox(None, Spinner(), None))
    caption = label(text, "body2")
    caption.setAlignment(Qt.AlignmentFlag.AlignCenter)
    page.body.addWidget(caption)


def message_box(icon_name: str, tone: str, title: str, text: str = "") -> BoxedList:
    box = BoxedList()
    box.add(Row(title, text, icon_name, tone))
    return box


class HomePage(Page):
    title = "Home"
    describe = Signal(str)
    pick_entry = Signal(object)
    pick_category = Signal(object)
    pick_diagnosis = Signal(object)

    def __init__(self, index, os_name: str):
        super().__init__()
        self.index = index
        self.os_name = os_name
        self.scan_results: list[engine.Diagnosis] | None = None
        self.pending: list[history.Record] = []
        self.body.addSpacing(16)
        self.body.addWidget(label("What's going wrong?", "title1"))
        self.body.addWidget(label("Describe it in your own words, or pick a category on the left.", "body2"))
        self.body.addSpacing(4)

        self.entry = QLineEdit()
        self.entry.setPlaceholderText("My game keeps flickering…")
        self.entry.setClearButtonEnabled(True)
        self.entry.returnPressed.connect(self._submit)
        self.check = button("Check my PC", "suggested")
        self.check.setEnabled(False)
        self.check.clicked.connect(self._submit)
        self.body.addLayout(hbox_stretch(self.entry, self.check))

        self.results = QWidget()
        self.results.setObjectName("transparent")
        self.results_layout = QVBoxLayout(self.results)
        self.results_layout.setContentsMargins(0, 0, 0, 0)
        self.results_layout.setSpacing(12)
        self.body.addWidget(self.results)

        self._timer = QTimer(self, singleShot=True, interval=120)
        self._timer.timeout.connect(self._refresh)
        self.entry.textChanged.connect(lambda _: self._timer.start())
        self._refresh()

    def set_scanning(self) -> None:
        self.scan_results = None
        self._refresh()

    def set_scan_results(self, results: list[engine.Diagnosis], pending: list[history.Record] = ()) -> None:
        self.scan_results = results
        self.pending = list(pending)
        self._refresh()

    def _submit(self) -> None:
        text = self.entry.text().strip()
        if text and self.index.search(text):
            self.describe.emit(text)

    def _section(self, text: str, note: str = "") -> None:
        header = QHBoxLayout()
        header.setContentsMargins(2, 12, 2, 0)
        header.setSpacing(8)
        header.addWidget(label(text, "section", wrap=False))
        header.addWidget(label(note, "caption"), 1)
        self.results_layout.addLayout(header)

    def _refresh(self) -> None:
        clear_layout(self.results_layout)
        query = self.entry.text().strip()
        if query:
            self._show_search(query)
        else:
            self._show_scan()

    def _show_search(self, query: str) -> None:
        hits = self.index.search(query, 12)
        hits = [h for h in hits if h.score >= hits[0].score * STRONG_MATCH]
        self.check.setEnabled(bool(hits))
        if not hits:
            self.results_layout.addWidget(message_box(
                "system-search", None, "Nothing matches that yet",
                "Try other words, or pick the closest category on the left."))
            return
        categories = [h for h in hits if h.is_category][:4]
        entries = [h for h in hits if not h.is_category][:6]
        if categories:
            chips = QHBoxLayout()
            chips.setSpacing(8)
            chips.setContentsMargins(0, 4, 0, 0)
            for hit in categories:
                chip = button(hit.target.title, "chip")
                chip.clicked.connect(lambda _=False, c=hit.target: self.pick_category.emit(c))
                chips.addWidget(chip)
            chips.addStretch(1)
            self.results_layout.addLayout(chips)
        if entries:
            self._section("Closest matches", "Pick one if it fits. If not, Check my PC looks through all of these.")
            box = BoxedList()
            for hit in entries:
                box.add(Row(hit.target.title, hit.target.summary, chevron=True,
                            on_click=lambda e=hit.target: self.pick_entry.emit(e)))
            self.results_layout.addWidget(box)

    def _show_scan(self) -> None:
        self.check.setEnabled(False)
        if self.pending:
            self._section("Waiting for a restart")
            box = BoxedList()
            for record in self.pending:
                box.add(Row(record.title, "Applied. It takes effect after you restart.", "system-reboot", "accent"))
            self.results_layout.addWidget(box)
        if self.scan_results is None:
            self._section("On this PC")
            box = BoxedList()
            box.add(Row("Checking this PC…", f"Looking at {self.os_name}'s hardware, drivers and logs. Nothing is changed.",
                        trailing=Spinner()))
            self.results_layout.addWidget(box)
        elif not self.scan_results:
            self._section("On this PC")
            self.results_layout.addWidget(message_box(
                "object-select", "success", "No known problems found",
                f"Everything we check automatically looks fine on {self.os_name}."))
        else:
            count = len(self.scan_results)
            self._section("Found on this PC", f"{count} recommended fix{'es' if count > 1 else ''}. Nothing is changed until you say so.")
            box = BoxedList()
            for d in self.scan_results:
                why = d.found[0].signal.says if d.found else d.entry.summary
                box.add(Row(d.entry.title, why, "dialog-warning", "warning", trailing=confidence_pill(d), chevron=True,
                            on_click=lambda x=d: self.pick_diagnosis.emit(x)))
            self.results_layout.addWidget(box)


def hbox_stretch(first, *rest) -> QHBoxLayout:
    layout = QHBoxLayout()
    layout.setSpacing(8)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.addWidget(first, 1)
    for item in rest:
        layout.addWidget(item)
    return layout


class CategoryPage(Page):
    check = Signal(object)
    pick_entry = Signal(object)

    def __init__(self, category: Category, entries: list[Entry], system):
        super().__init__()
        self.title = category.title
        self.body.addSpacing(8)
        self.body.addWidget(label(category.title, "title2"))
        self.body.addWidget(label(category.blurb, "body2"))
        go = button("Check my PC", "suggested")
        go.clicked.connect(lambda: self.check.emit(category))
        self.body.addSpacing(4)
        self.body.addLayout(hbox(go, None))
        self.section("Or pick a specific problem", "Not sure? Checking the whole category works fine.")
        box = BoxedList()
        ours = sorted(entries, key=lambda e: not system.in_scopes(e.systems))
        for entry in ours:
            fits = system.in_scopes(entry.systems)
            subtitle = entry.summary if fits else "For " + " and ".join(SCOPES[s] for s in entry.systems)
            box.add(Row(entry.title, subtitle, chevron=True, on_click=lambda e=entry: self.pick_entry.emit(e)))
        self.body.addWidget(box)


class ResultsPage(Page):
    title = "Results"
    pick = Signal(object)
    reproduce = Signal()

    def show_waiting(self, text="Reading your logs and hardware…") -> None:
        waiting(self, text)

    def show_results(self, heading: str, results: list[engine.Diagnosis], offer_reproduce: bool, reproduced: bool) -> None:
        self.reset()
        self.body.addSpacing(8)
        self.body.addWidget(label(heading, "title2"))
        live = [d for d in results if d.status == engine.APPLIES]
        likely = [d for d in live if d.recommended and d.found]
        rest = [d for d in live if d not in likely]
        fine = [d for d in results if d.status == engine.FIXED]

        if reproduced and not likely:
            self.body.addWidget(label("Nothing new showed up in the logs while you reproduced the problem.", "body2"))
        if likely:
            self.body.addWidget(label(f"{len(likely)} likely cause{'s' if len(likely) > 1 else ''}. Pick one to see what would change.", "body2"))
            box = BoxedList()
            for d in likely:
                box.add(Row(d.entry.title, d.found[0].signal.says, "dialog-warning", "warning",
                            trailing=confidence_pill(d), chevron=True, on_click=lambda x=d: self.pick.emit(x)))
            self.body.addWidget(box)
        elif not reproduced:
            self.body.addWidget(label("We didn't find anything conclusive in your logs.", "body2"))

        if offer_reproduce:
            self.body.addWidget(reproduce_card(self.reproduce.emit))

        if rest:
            self.section("Less likely", "Nothing on your PC points to these, but they could still be it.")
            box = BoxedList()
            for d in rest[:8]:
                box.add(Row(d.entry.title, d.entry.summary, trailing=confidence_pill(d), chevron=True,
                            on_click=lambda x=d: self.pick.emit(x)))
            self.body.addWidget(box)
        if fine:
            self.section("Ruled out", "We checked these, and they aren't the problem here.")
            box = BoxedList()
            for d in fine:
                box.add(Row(d.entry.title, icon_name="object-select", tone="success"))
            self.body.addWidget(box)
        if not live and not fine:
            self.body.addWidget(message_box("object-select", "success", "None of these apply to this PC",
                                            "This hardware or setup isn't affected by the problems we know about here."))


def reproduce_card(on_start, prompt: str | None = None) -> QWidget:
    box = BoxedList()
    start = button("Start")
    start.clicked.connect(lambda: on_start())
    box.add(Row("Show us the problem", prompt or "Make it happen once while we watch the logs. This usually settles it.",
                "view-refresh", "accent", trailing=start))
    return box


class ReproducePage(Page):
    title = "Show us the problem"
    done = Signal()
    cancel = Signal()

    def set_prompt(self, prompt: str) -> None:
        self.reset()
        self.body.addSpacing(8)
        self.body.addWidget(label("Make it happen now", "title2"))
        self.body.addWidget(label(prompt, "body2"))
        self.body.addSpacing(4)
        box = BoxedList()
        box.add(Row("Watching the system logs…", "Come back here when it has happened, even if it only happened once.",
                    trailing=Spinner()))
        self.body.addWidget(box)
        finished = button("It happened", "suggested")
        finished.clicked.connect(self.done.emit)
        back = button("Cancel")
        back.clicked.connect(self.cancel.emit)
        self.body.addSpacing(4)
        self.body.addLayout(hbox(None, back, finished))


def source_name(url: str) -> str:
    """'ArchWiki: Intel graphics' for an ArchWiki link, else the site's name."""
    parts = urlsplit(url)
    if parts.netloc == "wiki.archlinux.org" and parts.path.startswith("/title/"):
        page = unquote(parts.path.removeprefix("/title/")).replace("_", " ")
        section = unquote(parts.fragment).replace("_", " ")
        return f"ArchWiki: {page}" + (f", {section}" if section else "")
    return parts.netloc.removeprefix("www.")


class FixPage(Page):
    title = "Fix"
    apply = Signal(object, str, bool)  # plan, script, edited
    reproduce = Signal(object)
    open_url = Signal(str)

    def show_waiting(self) -> None:
        waiting(self, "Reading your logs and hardware…")

    def show_diagnosis(self, d: engine.Diagnosis, plan, plan_error: str | None, os_name: str) -> None:
        self.reset()
        self.diagnosis, self.plan = d, plan
        self._show_diagnosis(d, plan, plan_error, os_name)
        if d.entry.source:
            self.section("Learn more")
            box = BoxedList()
            box.add(Row("Read more about this problem", source_name(d.entry.source), "help-browser", chevron=True,
                        on_click=lambda url=d.entry.source: self.open_url.emit(url)))
            self.body.addWidget(box)

    def _show_diagnosis(self, d: engine.Diagnosis, plan, plan_error: str | None, os_name: str) -> None:
        entry = d.entry
        self.body.addSpacing(8)
        self.body.addWidget(label(entry.title, "title2"))
        self.body.addWidget(label(entry.summary, "body2"))

        if d.status == engine.WRONG_SYSTEM:
            scopes = " and ".join(SCOPES[s] for s in entry.systems)
            self.body.addWidget(message_box("dialog-warning", "warning", "This isn't meant for your system",
                                            f"It's written for {scopes}. You're on {os_name}, so nothing can be applied."))
            return
        if d.status == engine.NOT_APPLICABLE:
            self.body.addWidget(message_box("object-select", "success", "This doesn't apply to this PC",
                                            "Your hardware or setup isn't affected by this problem."))
            return
        if d.status == engine.FIXED:
            self.body.addWidget(message_box("object-select", "success", "This is already taken care of",
                                            "The fix is in place, or isn't needed on this setup."))
            return

        self.body.addWidget(self._confidence_box(d))
        self.body.addLayout(self._evidence(d))

        if entry.reproduce and any(s.spec.get("source") == "journal" for s in entry.signals) \
                and not any(f.evidence.in_window for f in d.found) and not d.recommended:
            self.body.addWidget(reproduce_card(lambda: self.reproduce.emit(d), entry.reproduce))

        if entry.advice and not entry.fixes:
            self.section("What to do", "We don't change anything automatically for this one.")
            box = BoxedList()
            box.add(Row(entry.advice, icon_name="dialog-warning"))
            self.body.addWidget(box)
            return
        if plan_error:
            self.body.addWidget(message_box("object-select", "success", "Nothing to change", plan_error))
            return
        if not plan:
            scopes = " and ".join(SCOPES[s] for s in entry.systems)
            self.body.addWidget(message_box("dialog-warning", "warning", "No fix for your system yet",
                                            f"Fixes exist for {scopes}. You're on {os_name}."))
            return
        self._show_plan(d, plan, os_name)

    def _confidence_box(self, d: engine.Diagnosis) -> BoxedList:
        box = BoxedList()
        inner = QFrame()
        inner.setObjectName("row")
        column = QVBoxLayout(inner)
        column.setContentsMargins(14, 12, 14, 14)
        column.setSpacing(8)
        percent = label(f"{d.confidence}%", "title1", wrap=False)
        words = {"High": "High confidence this is your problem",
                 "Medium": "Medium confidence this is your problem",
                 "Low": "Low confidence: little on your PC confirms this"}[d.level]
        top = QHBoxLayout()
        top.setSpacing(12)
        top.addWidget(percent)
        top.addWidget(label(words, "strong"), 1)
        column.addLayout(top)
        column.addWidget(Meter(d.confidence, LEVEL_ROLE[d.level]))
        box.add(inner)
        return box

    def _evidence(self, d: engine.Diagnosis) -> QVBoxLayout:
        layout = QVBoxLayout()
        layout.setSpacing(8)
        layout.setContentsMargins(0, 0, 0, 0)
        header = QHBoxLayout()
        header.setContentsMargins(2, 8, 2, 0)
        header.addWidget(label("What we found", "section", wrap=False))
        header.addStretch(1)
        layout.addLayout(header)
        box = BoxedList()
        if not d.found:
            box.add(Row("Nothing on your PC confirms this yet", "It's listed because it matches what you described."))
        for finding in d.found:
            subtitle = "Appeared while you reproduced the problem" if finding.evidence.in_window else ""
            row = Row(finding.signal.says, subtitle, "system-search", "accent")
            for line in finding.evidence.lines[:2]:
                row.extra.addWidget(label(line, "mono", selectable=True))
            box.add(row)
        layout.addWidget(box)
        if d.unread:
            layout.addWidget(label(f"{d.unread} check{'s' if d.unread > 1 else ''} couldn't run here, for example because a log isn't readable.", "caption"))
        return layout

    def _show_plan(self, d: engine.Diagnosis, plan, os_name: str) -> None:
        scopes = " and ".join(SCOPES[s] for s in d.fix.systems)
        self.section("What will change", f"Written for {scopes}. You're on {os_name}.")
        box = BoxedList()
        for number, says in enumerate(plan.summary, 1):
            box.add(Row(says, leading=number_tile(number)))
        box.add(Row("Files are backed up first, and you can undo this from Applied fixes.", icon_name="edit-undo"))
        if plan.reboot:
            box.add(Row("You'll need to restart afterwards.", icon_name="system-reboot"))
        self.body.addWidget(box)

        self.body.addSpacing(4)
        self.advanced = CheckBox(ADVANCED_WARNING, "danger", bold=True)
        self.body.addWidget(self.advanced)
        self.editor = QPlainTextEdit(plan.script)
        self.editor.setProperty("editing", True)
        self.editor.setMinimumHeight(320)
        self.editor.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.editor.setVisible(False)
        self.body.addWidget(self.editor)
        self.reset_script = button("Reset to the original patch")
        self.reset_script.clicked.connect(lambda: self.editor.setPlainText(plan.script))
        self.edit_note = label("This runs as root exactly as written. Undo only reverses the original patch's changes, so edits you add may stay.", "danger")
        self.edit_row = hbox(self.reset_script, None)
        self.edit_note.setVisible(False)
        self.reset_script.setVisible(False)
        self.body.addWidget(self.edit_note)
        self.body.addLayout(self.edit_row)
        self.advanced.toggled.connect(self._toggle_advanced)
        self.editor.textChanged.connect(self._sync_apply)

        self.low_note = None
        if not d.recommended:
            self.low_note = label("Confidence is low. This may not be your problem, so only apply it if the description fits.", "warning")
            self.body.addWidget(self.low_note)

        self.apply_button = button("Apply fix", "suggested" if d.recommended else None)
        self.apply_button.clicked.connect(self._apply)
        self.body.addSpacing(4)
        self.body.addLayout(hbox(None, self.apply_button))

    def _toggle_advanced(self, on: bool) -> None:
        self.editor.setVisible(on)
        self.reset_script.setVisible(on)
        self.edit_note.setVisible(on)
        if not on:
            self.editor.setPlainText(self.plan.script)
        self._sync_apply()

    def _edited(self) -> bool:
        return self.advanced.isChecked() and self.editor.toPlainText() != self.plan.script

    def _sync_apply(self) -> None:
        edited = self._edited()
        self.apply_button.setText("Apply edited patch" if edited else "Apply fix")
        self.apply_button.setProperty("kind", "danger" if edited else ("suggested" if self.diagnosis.recommended else None))
        repolish(self.apply_button)
        self.apply_button.setEnabled(bool(self.editor.toPlainText().strip()))

    def _apply(self) -> None:
        self.apply.emit(self.plan, self.editor.toPlainText(), self._edited())


class ApplyPage(Page):
    title = "Applying"
    done = Signal()
    undo = Signal(object)
    reboot = Signal()

    def start(self, heading: str) -> None:
        self.reset()
        self.body.addSpacing(8)
        self.heading = label(heading, "title2")
        self.body.addWidget(self.heading)
        self.status_row = QHBoxLayout()
        self.status_row.setSpacing(8)
        self.spinner = Spinner()
        self.status = label("Waiting for your password…", "body2")
        self.status_row.addWidget(self.spinner)
        self.status_row.addWidget(self.status, 1)
        self.body.addLayout(self.status_row)
        self.console = QPlainTextEdit()
        self.console.setReadOnly(True)
        self.console.setMinimumHeight(340)
        self.console.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.body.addWidget(self.console)
        self.actions = QHBoxLayout()
        self.actions.setSpacing(8)
        self.actions.addStretch(1)
        self.body.addLayout(self.actions)

    def add_output(self, text: str) -> None:
        self.console.moveCursor(self.console.textCursor().MoveOperation.End)
        self.console.insertPlainText(text)
        self.console.ensureCursorVisible()
        steps = [line[4:] for line in text.splitlines() if line.startswith("==> ")]
        if steps:
            self.status.setText(steps[-1])

    def finish(self, role: str, text: str, record: history.Record | None = None, can_reboot=False) -> None:
        self.spinner.setVisible(False)
        self.status.setProperty("role", role)
        self.status.setText(text)
        repolish(self.status)
        if record:
            undo = button("Undo")
            undo.clicked.connect(lambda: self.undo.emit(record))
            self.actions.addWidget(undo)
        close = button("Done", None if can_reboot else "suggested")
        close.clicked.connect(self.done.emit)
        self.actions.addWidget(close)
        if can_reboot:
            restart = button("Restart now", "suggested")
            restart.clicked.connect(self.reboot.emit)
            self.actions.addWidget(restart)


class HistoryPage(Page):
    title = "Applied fixes"
    undo = Signal(object)

    def show_records(self, records: list[history.Record]) -> None:
        self.reset()
        self.body.addSpacing(8)
        self.body.addWidget(label("Applied fixes", "title2"))
        self.body.addWidget(label("Everything Troubleshooter changed on this PC.", "body2"))
        if not records:
            self.body.addWidget(message_box("document-open-recent", None, "Nothing yet", "Fixes you apply show up here, and you can undo them."))
            return
        box = BoxedList()
        for record in records:
            when = time.strftime("%d %b %Y, %H:%M", time.localtime(record.time))
            state = {history.APPLIED: "Applied", history.FAILED: "Failed partway", history.UNDONE: "Undone"}[record.status]
            subtitle = f"{state} on {when}" + (". Edited by you" if record.edited else "")
            trailing = None
            if record.status != history.UNDONE:
                trailing = button("Undo")
                trailing.clicked.connect(lambda _=False, r=record: self.undo.emit(r))
            icon_name, tone = {history.APPLIED: ("object-select", "success"), history.FAILED: ("dialog-error", "danger"),
                               history.UNDONE: ("edit-undo", None)}[record.status]
            box.add(Row(record.title, subtitle, icon_name, tone, trailing=trailing))
        self.body.addWidget(box)
