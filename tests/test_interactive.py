"""Drives the terminal version with typed answers against a fake machine. Scripts run with stub binaries."""
import io
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests.test_ui import GRUB, problem_machine
from troubleshooter import __main__ as launcher
from troubleshooter import history, interactive
from troubleshooter.database import load_database


class Answers:
    """Types the given answers in order, then Ctrl+D."""

    def __init__(self, *answers):
        self.answers = list(answers)
        self.prompts = []

    def __call__(self, prompt):
        self.prompts.append(prompt)
        if not self.answers:
            raise EOFError
        return self.answers.pop(0)


class InteractiveTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db = load_database()

    def setUp(self):
        self.m = problem_machine()
        self.addCleanup(self.m.close)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.hist = history.History(Path(self.tmp.name) / "h.json")
        self.dismissed = history.Dismissed(Path(self.tmp.name) / "dismissed.json")

    def run_session(self, *answers, elevate=(), editor=None, machine=None):
        self.out = io.StringIO()
        self.answers = Answers(*answers)
        self.session = interactive.Session(self.db, make_system=(machine or self.m).system, hist=self.hist,
                                           dismissed=self.dismissed, elevate=list(elevate), ask=self.answers,
                                           out=self.out, editor=editor, color=False)
        self.code = self.session.run()
        return self.out.getvalue()

    def stub(self, name, body):
        path = self.m.stubs / name
        path.write_text("#!/bin/sh\n" + body)
        path.chmod(path.stat().st_mode | stat.S_IXUSR)
        return str(path)

    def number_of(self, entry_id):
        """Where the launch check lists an entry, from a dry run."""
        self.run_session()
        return str([d.entry.id for d in self.session.split_found()[0]].index(entry_id) + 1)

    def category_path(self, category_id, entry_id):
        """What to type at Categories to open an entry."""
        system = self.m.system()
        entries = sorted(self.db.in_category(category_id), key=lambda e: not system.in_scopes(e.systems))
        return str(list(self.db.categories).index(category_id) + 1), str([e.id for e in entries].index(entry_id) + 1)

    def test_recommendations_appear_on_launch(self):
        out = self.run_session()
        self.assertEqual(self.code, 0)
        self.assertIn("Found on this PC", out)
        self.assertIn(self.db.entries["nvidia-sleep"].title, out)
        self.assertIn("Nothing is changed until you say so.", out)

    def test_q_quits_from_any_screen(self):
        self.run_session("c", "1", "q", "never typed")
        self.assertEqual(self.code, 0)
        self.assertEqual(self.answers.answers, ["never typed"])

    def test_a_sentence_searches_and_check_finds_the_cause(self):
        out = self.run_session("screen is black after I wake my laptop from sleep", "k")
        self.assertIn("Closest matches", out)
        self.assertIn(self.db.entries["nvidia-sleep"].title, out)
        self.assertIn("likely cause", out)

    def test_gibberish_says_so(self):
        out = self.run_session("qwertyzxcv")
        self.assertIn("Nothing matches that yet", out)

    def test_category_check(self):
        out = self.run_session("c", self.category_path("display", "nvidia-sleep")[0], "k")
        self.assertIn("Display check", out)
        self.assertIn("likely cause", out)

    def test_wrong_input_asks_again_without_redrawing(self):
        out = self.run_session("c", "zz", "999")
        self.assertEqual(out.count("Categories\n"), 1)
        self.assertEqual(out.count("Pick one of the options above."), 2)

    def test_fix_screen_tells_what_changes_with_percent(self):
        number = self.number_of("nvidia-sleep")
        out = self.run_session(number)
        self.assertRegex(out, r"\d+%  (High|Medium) confidence this is your problem")
        self.assertIn("Add kernel parameters that save video memory during sleep", out)
        self.assertIn("You'll need to restart afterwards.", out)
        self.assertIn(self.db.entries["nvidia-sleep"].source, out)

    def test_view_shows_the_exact_script(self):
        number = self.number_of("nvidia-sleep")
        out = self.run_session(number, "v")
        plan = self.session.plan_for(self.session.found[int(number) - 1])[0]
        self.assertIn("  " + plan.script.splitlines()[1], out)

    def test_apply_then_undo_changes_and_restores_the_fake_system(self):
        number = self.number_of("nvidia-sleep")
        out = self.run_session(number, "a", "y")
        self.assertIn("Done. Restart your PC to finish.", out)
        self.assertIn("r) Restart now", out)
        self.assertIn("nvidia.NVreg_PreserveVideoMemoryAllocations=1", self.m.read("/etc/default/grub"))
        self.assertIn("mkinitcpio -P", self.m.commands_run())
        self.assertEqual([r.status for r in self.hist.load()], [history.APPLIED])

        out = self.run_session("h", "1", "y")
        self.assertIn("Undone. Restart your PC to finish.", out)
        self.assertEqual(self.m.read("/etc/default/grub"), GRUB)
        self.assertEqual(self.hist.load()[0].status, history.UNDONE)

    def test_undo_right_after_applying(self):
        number = self.number_of("nvidia-sleep")
        out = self.run_session(number, "a", "y", "u")
        self.assertIn("Undone.", out)
        self.assertEqual(self.m.read("/etc/default/grub"), GRUB)

    def test_declining_changes_nothing(self):
        number = self.number_of("nvidia-sleep")
        self.run_session(number, "a", "n")
        self.assertEqual(self.m.read("/etc/default/grub"), GRUB)
        self.assertEqual(self.hist.load(), [])

    def test_done_goes_home_and_the_fix_waits_for_a_restart(self):
        self.m.write("/proc/stat", "cpu 0\nbtime 1000\n")
        number = self.number_of("nvidia-sleep")
        out = self.run_session(number, "a", "y", "")
        self.assertIn("Waiting for a restart", out.rsplit("Troubleshooter · ", 1)[1])
        self.assertNotIn("nvidia-sleep", [d.entry.id for d in self.session.found])

    def test_edited_script_is_what_runs(self):
        number = self.number_of("nvidia-sleep")
        marker = self.m.root / "edited-marker"
        editor = f"sh -c 'printf \"touch {marker}\\necho \\\"==> Done\\\"\\n\" > \"$0\"'"
        out = self.run_session(number, "e", "y", "a", "y", editor=editor)
        self.assertIn(interactive.ADVANCED_WARNING, out)
        self.assertIn("a) Apply edited patch", out)
        self.assertIn("This runs as root exactly as written.", out)
        self.assertTrue(marker.exists())
        self.assertEqual(self.m.read("/etc/default/grub"), GRUB)
        self.assertTrue(self.hist.load()[0].edited)

    def test_reset_brings_back_the_original_patch(self):
        number = self.number_of("nvidia-sleep")
        self.run_session(number, "e", "y", "o", "a", "y", editor="sh -c 'echo true > \"$0\"'")
        self.assertIn("nvidia.NVreg_PreserveVideoMemoryAllocations=1", self.m.read("/etc/default/grub"))
        self.assertFalse(self.hist.load()[0].edited)

    def test_failed_script_is_reported_and_undoable(self):
        number = self.number_of("nvidia-sleep")
        self.stub("systemctl", "exit 1\n")
        out = self.run_session(number, "a", "y")
        self.assertIn("This fix failed partway.", out)
        self.assertIn("u) Undo", out)
        self.assertEqual(self.hist.load()[0].status, history.FAILED)

    def test_sudo_is_asked_first_and_a_wrong_password_changes_nothing(self):
        number = self.number_of("nvidia-sleep")
        sudo = self.stub("sudo", 'echo "sudo $*" >> "%s"\n[ "$1" = -v ] && exit 1\nexit 0\n' % self.m.log)
        out = self.run_session(number, "a", "y", elevate=[sudo])
        self.assertIn("Cancelled. Nothing was changed.", out)
        self.assertEqual(self.m.commands_run(), ["sudo -v"])
        self.assertEqual(self.m.read("/etc/default/grub"), GRUB)
        self.assertEqual(self.hist.load(), [])

    def test_sudo_runs_the_script_without_asking_again(self):
        number = self.number_of("nvidia-sleep")
        sudo = self.stub("sudo", 'echo "sudo $1" >> "%s"\ncase "$1" in -v) exit 0 ;; -n) shift; exec "$@" ;; esac\nexit 1\n'
                         % self.m.log)
        out = self.run_session(number, "a", "y", elevate=[sudo])
        self.assertIn("Done.", out)
        self.assertEqual([c for c in self.m.commands_run() if c.startswith("sudo")], ["sudo -v", "sudo -n", "sudo -n"])
        self.assertIn("nvidia.NVreg_PreserveVideoMemoryAllocations=1", self.m.read("/etc/default/grub"))

    def test_dismissed_pkexec_prompt_changes_nothing(self):
        number = self.number_of("nvidia-sleep")
        out = self.run_session(number, "a", "y", elevate=[self.stub("fake-pkexec", "exit 126\n")])
        self.assertIn("Cancelled. Nothing was changed.", out)
        self.assertEqual(self.hist.load(), [])

    def test_missing_permission_tool_is_reported(self):
        number = self.number_of("nvidia-sleep")
        out = self.run_session(number, "a", "y", elevate=["/nonexistent/pkexec"])
        self.assertIn("Couldn't ask for permission to make changes.", out)
        self.assertEqual(self.hist.load(), [])

    def test_ctrl_c_does_not_stop_a_running_fix(self):
        number = self.number_of("nvidia-sleep")
        marker = self.m.root / "finished"
        editor = f"sh -c 'printf \"kill -INT $PPID\\nsleep 0.3\\ntouch {marker}\\n\" > \"$0\"'"
        out = self.run_session(number, "e", "y", "a", "y", editor=editor)
        self.assertIn("Still working. Please wait for this to finish.", out)
        self.assertTrue(marker.exists())
        self.assertEqual(self.hist.load()[0].status, history.APPLIED)

    def test_dismissed_recommendation_stays_hidden_until_shown_again(self):
        number = self.number_of("nvidia-sleep")
        out = self.run_session(number, "d", "b")
        self.assertIn("It won't be recommended on launch anymore.", out)
        self.assertEqual(self.dismissed.ids(), {"nvidia-sleep"})
        self.assertNotIn("nvidia-sleep", [d.entry.id for d in self.session.split_found()[0]])

        out = self.run_session("d", "1")
        self.assertIn("1 still found on this PC, but not recommended.", out)
        self.assertEqual(self.dismissed.ids(), set())

    def test_only_launch_recommendations_can_be_dismissed(self):
        out = self.run_session("c", *self.category_path("games", "multilib-disabled"))
        self.assertNotIn("Don't recommend this again", out)

    def test_reproduce_uses_only_new_logs(self):
        self.m.modules("amdgpu")

        def answer_after_watching(prompt):
            self.m.journal("kernel: amdgpu 0000:05:00.0: ring gfx timeout\n", window=True)
            return ""
        script = ["c", *self.category_path("amd", "amd-ring-timeout"), "p", answer_after_watching]

        def ask(prompt):
            if not script:
                raise EOFError
            item = script.pop(0)
            return item(prompt) if callable(item) else item
        self.out = io.StringIO()
        session = interactive.Session(self.db, make_system=self.m.system, hist=self.hist, dismissed=self.dismissed,
                                      elevate=[], ask=ask, out=self.out, color=False)
        session.run()
        out = self.out.getvalue()
        self.assertIn("Make it happen now", out)
        self.assertIn("Appeared while you reproduced the problem", out)

    def test_wrong_distro_cannot_apply(self):
        self.m.write("/etc/os-release", 'ID=debian\nNAME="Debian"\n')
        out = self.run_session("c", *self.category_path("nvidia", "nvidia-sleep"), "a")
        self.assertIn("This isn't meant for your system", out)
        self.assertNotIn("a) Apply fix", out)
        self.assertEqual(self.hist.load(), [])

    def test_empty_history(self):
        out = self.run_session("h")
        self.assertIn("Fixes you apply show up here, and you can undo them.", out)

    def test_ctrl_d_quits(self):
        self.run_session()
        self.assertEqual(self.code, 0)


class LauncherTest(unittest.TestCase):
    def test_terminal_version_never_loads_qt(self):
        code = "import sys, troubleshooter.interactive; sys.exit('PySide6' in sys.modules)"
        self.assertEqual(subprocess.run([sys.executable, "-c", code]).returncode, 0)

    def launch(self, env, tty=True):
        with mock.patch.dict(os.environ, env, clear=True), mock.patch.object(sys, "argv", ["troubleshooter"]), \
                mock.patch.object(sys.stdin, "isatty", return_value=tty), \
                mock.patch("troubleshooter.interactive.main", return_value=0) as cli, \
                mock.patch("sys.stderr", new_callable=io.StringIO) as err:
            code = launcher.main()
        return code, cli.called, err.getvalue()

    def test_no_desktop_starts_the_terminal_version(self):
        code, started, err = self.launch({})
        self.assertEqual((code, started), (0, True))
        self.assertIn("Starting the terminal version instead.", err)

    def test_no_desktop_and_no_terminal_says_why(self):
        code, started, err = self.launch({}, tty=False)
        self.assertEqual((code, started), (1, False))
        self.assertIn("No desktop", err)

    def test_window_failing_to_start_falls_back(self):
        with mock.patch("troubleshooter.ui.app.run", side_effect=AttributeError("broken")):
            code, started, err = self.launch({"WAYLAND_DISPLAY": "wayland-0"})
        self.assertEqual((code, started), (0, True))
        self.assertIn("AttributeError: broken", err)
        self.assertIn("The window failed to start.", err)


if __name__ == "__main__":
    unittest.main()
