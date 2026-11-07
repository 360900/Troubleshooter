"""Drives the real window headlessly against a fake machine. Scripts run with stub binaries."""
import gc
import os
import stat
import tempfile
import time
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEventLoop, QTimer

from tests.fakes import CACHY, FakeMachine
from troubleshooter import history
from troubleshooter.database import load_database
from troubleshooter.ui import pages
from troubleshooter.ui.app import create_app
from troubleshooter.ui.window import MainWindow

GRUB = 'GRUB_CMDLINE_LINUX_DEFAULT="quiet"\n'


def problem_machine():
    m = FakeMachine(CACHY)
    m.install_stubs()
    m.pci("0000:01:00.0", "10de", "1c03", "030000", "nvidia")
    m.packages = {"grub", "nvidia-open-dkms", "nvidia-utils", "linux-cachyos"}
    m.modules("nvidia", "nvidia_drm")
    m.write("/etc/default/grub", GRUB)
    m.write("/proc/driver/nvidia/params", "PreserveVideoMemoryAllocations: 0\nUseKernelSuspendNotifiers: 0\n")
    m.write("/sys/module/nvidia_drm/parameters/modeset", "Y\n")
    m.write("/usr/lib/modules/7.0/pkgbase", "linux-cachyos\n")
    return m


class UiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = create_app()
        cls.db = load_database()

    def setUp(self):
        self.m = problem_machine()
        self.addCleanup(self.m.close)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.hist = history.History(Path(self.tmp.name) / "h.json")
        self.window = self.make_window([])
        self.settle()

    def make_window(self, elevate):
        window = MainWindow(self.db, make_system=self.m.system, hist=self.hist, elevate=elevate)
        self.addCleanup(window.deleteLater)
        window.show()
        return window

    def wait_until(self, condition, timeout=8.0):
        end = time.monotonic() + timeout
        while not condition():
            self.assertLess(time.monotonic(), end, "timed out")
            loop = QEventLoop()
            QTimer.singleShot(10, loop.quit)
            loop.exec()

    def settle(self):
        self.wait_until(lambda: self.window.home.scan_results is not None and not self.window.tasks)

    def texts(self, page):
        return [w.text() for w in page.findChildren(pages.QWidget) if hasattr(w, "text") and callable(w.text)]

    def open_fix(self, entry_id):
        self.window.check_entry(self.db.entries[entry_id])
        self.wait_until(lambda: hasattr(self.window.fix, "diagnosis") and not self.window.tasks
                        and self.window.fix.diagnosis.entry.id == entry_id)

    def test_garbage_is_only_collected_on_the_gui_thread(self):
        self.assertFalse(gc.isenabled())
        for _ in range(gc.get_threshold()[0] * 2):
            cycle = []
            cycle.append(cycle)
        self.wait_until(lambda: gc.get_count()[0] <= gc.get_threshold()[0])

    def test_recommendations_appear_on_launch_without_asking(self):
        ids = [d.entry.id for d in self.window.home.scan_results]
        self.assertIn("nvidia-open-on-legacy", ids)
        self.assertIn("nvidia-sleep", ids)
        self.assertIn("nvidia-open-on-legacy" and self.db.entries["nvidia-sleep"].title, self.texts(self.window.home))

    def test_search_works_from_a_sentence(self):
        self.window.home.entry.setText("screen is black after I wake my laptop from sleep")
        self.wait_until(self.window.home.check.isEnabled)
        self.assertIn(self.db.entries["nvidia-sleep"].title, self.texts(self.window.home))
        self.window.home.check.click()
        self.wait_until(lambda: self.window.stack.currentWidget() is self.window.results and not self.window.tasks
                        and any("likely cause" in t for t in self.texts(self.window.results)))

    def test_gibberish_says_so(self):
        self.window.home.entry.setText("qwertyzxcv")
        self.wait_until(lambda: "Nothing matches that yet" in self.texts(self.window.home))
        self.assertFalse(self.window.home.check.isEnabled())

    def test_category_can_be_checked_without_picking_an_entry(self):
        self.window.open_category(self.db.categories["display"])
        self.assertIs(self.window.stack.currentWidget(), self.window.category_page)
        self.window.check_category(self.db.categories["display"])
        self.wait_until(lambda: not self.window.tasks and self.window.stack.currentWidget() is self.window.results
                        and any("likely cause" in t for t in self.texts(self.window.results)))

    def test_fix_page_tells_what_changes_with_percent(self):
        self.open_fix("nvidia-sleep")
        texts = self.texts(self.window.fix)
        self.assertTrue(any(t.endswith("%") for t in texts), texts)
        self.assertIn("Add kernel parameters that save video memory during sleep", texts)
        self.assertIn("You'll need to restart afterwards.", texts)

    def test_source_link_opens_the_page(self):
        self.open_fix("nvidia-sleep")
        opened = []
        self.window.fix.open_url.connect(opened.append)
        rows = [r for r in self.window.fix.findChildren(pages.Row) if r.title.text() == "Read more about this problem"]
        self.assertEqual(len(rows), 1)
        self.assertTrue(rows[0].subtitle.text().startswith("ArchWiki: NVIDIA"))
        rows[0].clicked.emit()
        self.assertEqual(opened, [self.db.entries["nvidia-sleep"].source])

    def test_source_names(self):
        self.assertEqual(pages.source_name("https://wiki.archlinux.org/title/Intel_graphics#Screen_flickering"),
                         "ArchWiki: Intel graphics, Screen flickering")
        self.assertEqual(pages.source_name("https://github.com/360900/mbp132-setup#what-it-fixes-and-why"),
                         "GitHub: 360900/mbp132-setup")
        self.assertEqual(pages.source_name("https://www.kernel.org/doc/html/latest/"), "kernel.org")

    def test_advanced_warning_is_exact_and_gates_the_editor(self):
        self.open_fix("nvidia-sleep")
        fix = self.window.fix
        self.assertEqual(fix.advanced.text(), "ADVANCED! Check this box ONLY if you know exactly what you are doing!")
        self.assertEqual(fix.advanced.role, "danger")
        self.assertFalse(fix.editor.isVisible())
        fix.advanced.setChecked(True)
        self.assertTrue(fix.editor.isVisible())
        self.assertEqual(fix.editor.toPlainText(), fix.plan.script)
        fix.editor.setPlainText(fix.plan.script + "echo edited\n")
        self.assertEqual(fix.apply_button.text(), "Apply edited patch")
        fix.reset_script.click()
        self.assertEqual(fix.apply_button.text(), "Apply fix")
        fix.editor.setPlainText("echo edited\n")
        fix.advanced.setChecked(False)
        self.assertEqual(fix.editor.toPlainText(), fix.plan.script)

    def _apply_and_wait(self):
        self.window.fix.apply_button.click()
        self.wait_until(lambda: self.window.process is None and any(
            t in ("Done.", "Done. Restart your PC to finish.") or "failed partway" in t
            or "Cancelled" in t for t in self.texts(self.window.apply)))

    def test_apply_then_undo_changes_and_restores_the_fake_system(self):
        self.open_fix("nvidia-sleep")
        self._apply_and_wait()
        self.assertIn("nvidia.NVreg_PreserveVideoMemoryAllocations=1", self.m.read("/etc/default/grub"))
        self.assertIn("mkinitcpio -P", self.m.commands_run())
        records = self.hist.load()
        self.assertEqual([r.status for r in records], [history.APPLIED])

        self.window.undo_record(records[0])
        self.wait_until(lambda: self.window.process is None and any(t.startswith("Undone") for t in self.texts(self.window.apply)))
        self.assertEqual(self.m.read("/etc/default/grub"), GRUB)
        self.assertEqual(self.hist.load()[0].status, history.UNDONE)

    def test_edited_script_is_what_runs(self):
        self.open_fix("nvidia-sleep")
        fix = self.window.fix
        fix.advanced.setChecked(True)
        marker = self.m.root / "edited-marker"
        fix.editor.setPlainText(f"#!/bin/bash\ntouch {marker}\necho '==> Done'\n")
        self._apply_and_wait()
        self.assertTrue(marker.exists())
        self.assertEqual(self.m.read("/etc/default/grub"), GRUB)
        self.assertTrue(self.hist.load()[0].edited)

    def test_failed_script_is_reported_and_undoable(self):
        self.open_fix("nvidia-sleep")
        (self.m.stubs / "systemctl").write_text("#!/bin/sh\nexit 1\n")
        self._apply_and_wait()
        self.assertEqual(self.hist.load()[0].status, history.FAILED)

    def _fake_pkexec(self, code):
        fake = self.m.stubs / "fake-pkexec"
        fake.write_text(f"#!/bin/sh\nexit {code}\n")
        fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
        return str(fake)

    def test_cancelled_password_dialog_changes_nothing(self):
        self.window = self.make_window([self._fake_pkexec(126)])
        self.settle()
        self.open_fix("nvidia-sleep")
        self._apply_and_wait()
        self.assertEqual(self.m.read("/etc/default/grub"), GRUB)
        self.assertEqual(self.hist.load(), [])

    def test_missing_pkexec_is_reported(self):
        self.window = self.make_window(["/nonexistent/pkexec"])
        self.settle()
        self.open_fix("nvidia-sleep")
        self.window.fix.apply_button.click()
        self.wait_until(lambda: any("permission" in t for t in self.texts(self.window.apply)))
        self.assertIsNone(self.window.process)

    def test_reproduce_flow_uses_only_new_logs(self):
        self.m.modules("amdgpu")
        self.open_fix("amd-ring-timeout")
        before = self.window.fix.diagnosis.confidence
        self.window.start_reproduce()
        self.assertIs(self.window.stack.currentWidget(), self.window.reproduce)
        self.wait_until(lambda: self.window.cursor is not None)
        self.m.journal("kernel: amdgpu 0000:05:00.0: ring gfx timeout\n", window=True)
        self.window.finish_reproduce()
        self.wait_until(lambda: not self.window.tasks and self.window.stack.currentWidget() is self.window.fix
                        and self.window.fix.diagnosis.confidence > before)
        self.assertIn("Appeared while you reproduced the problem", self.texts(self.window.fix))
        self.assertIsNone(self.window.cursor)

    def test_reproducing_from_a_category_with_nothing_conclusive(self):
        m = problem_machine()
        self.addCleanup(m.close)
        m.modules("amdgpu")
        m.write("/proc/driver/nvidia/params", "UseKernelSuspendNotifiers: 1\n")
        self.m = m
        self.window = self.make_window([])
        self.settle()
        self.window.check_category(self.db.categories["crashes"])
        self.wait_until(lambda: not self.window.tasks and "Show us the problem" in self.texts(self.window.results))
        self.window.start_reproduce()
        self.wait_until(lambda: self.window.cursor is not None)
        m.journal("kernel: amdgpu 0000:05:00.0: ring gfx timeout\n", window=True)
        self.window.finish_reproduce()
        self.wait_until(lambda: not self.window.tasks and self.window.stack.currentWidget() is self.window.results
                        and self.db.entries["amd-ring-timeout"].title in self.texts(self.window.results)
                        and any("likely cause" in t for t in self.texts(self.window.results)))

    def test_active_sidebar_row_is_the_only_highlighted_one(self):
        self.window.open_category(self.db.categories["sound"])
        active = [b.caption.text() for b in self.window.nav_buttons if b.caption.property("role") == "navActive"]
        self.assertEqual(active, ["Sound"])

    def test_wrong_distro_cannot_apply(self):
        self.m.write("/etc/os-release", 'ID=debian\nNAME="Debian"\n')
        self.window = self.make_window([])
        self.settle()
        self.open_fix("nvidia-sleep")
        self.assertIn("This isn't meant for your system", self.texts(self.window.fix))

    def test_back_returns_and_cancels_stale_work(self):
        self.window.open_category(self.db.categories["nvidia"])
        self.window.check_entry(self.db.entries["nvidia-sleep"])
        self.window.go_back()
        self.assertIs(self.window.stack.currentWidget(), self.window.category_page)
        self.wait_until(lambda: not self.window.tasks)
        self.assertIs(self.window.stack.currentWidget(), self.window.category_page)

    def test_navigation_is_locked_while_a_fix_runs(self):
        (self.m.stubs / "slow").write_text("")
        self.open_fix("nvidia-sleep")
        fix = self.window.fix
        fix.advanced.setChecked(True)
        fix.editor.setPlainText("sleep 0.3\necho '==> Done'\n")
        fix.apply_button.click()
        self.window.go_home()
        self.assertIs(self.window.stack.currentWidget(), self.window.apply)
        self.wait_until(lambda: self.window.process is None)

    def test_window_refuses_to_close_while_a_fix_runs(self):
        self.open_fix("nvidia-sleep")
        fix = self.window.fix
        fix.advanced.setChecked(True)
        fix.editor.setPlainText("sleep 0.3\necho '==> Done'\n")
        fix.apply_button.click()
        self.assertFalse(self.window.close())
        self.assertTrue(self.window.isVisible())
        self.wait_until(lambda: self.window.process is None)
        self.assertTrue(self.window.close())

    def test_fix_waiting_for_restart_is_not_recommended_again(self):
        self.m.write("/proc/stat", "cpu 0\nbtime 1000\n")
        self.open_fix("nvidia-sleep")
        self._apply_and_wait()
        self.wait_until(lambda: not self.window.tasks and self.window.home.scan_results is not None)
        self.assertNotIn("nvidia-sleep", [d.entry.id for d in self.window.home.scan_results])
        self.assertEqual([r.entry_id for r in self.window.home.pending], ["nvidia-sleep"])
        self.window.go_home()
        self.wait_until(lambda: "Waiting for a restart" in self.texts(self.window.home))
        # After a reboot, the record no longer counts as pending.
        self.m.write("/proc/stat", f"btime {int(time.time()) + 60}\n")
        self.assertEqual(self.hist.pending_restart(self.m.system().boot_time), [])

    def test_theme_switch_survives_pages_that_were_rebuilt(self):
        import sys
        from troubleshooter.ui.theme import theme
        errors = []
        hook = sys.excepthook
        sys.excepthook = lambda *e: errors.append(e)
        self.addCleanup(setattr, sys, "excepthook", hook)
        for category in self.db.categories.values():
            self.window.open_category(category)
        self.open_fix("nvidia-sleep")
        self.window.go_home()
        self.wait_until(lambda: True)
        from PySide6.QtCore import QCoreApplication, QEvent
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        theme.refresh(self.app)
        self.assertEqual(errors, [])

    def test_both_themes_recolor_the_window(self):
        from troubleshooter.ui.theme import theme
        self.addCleanup(lambda: (setattr(theme, "is_dark", type(theme).is_dark.__get__(theme)), theme.refresh(self.app)))
        canvases = {}
        for dark in (True, False):
            theme.is_dark = lambda app, value=dark: value
            theme.refresh(self.app)
            self.wait_until(lambda: True)
            self.assertEqual(theme.dark, dark)
            canvases[dark] = theme.c["canvas"].hex
            self.assertFalse(self.window.grab().isNull())
        self.assertNotEqual(canvases[True], canvases[False])
