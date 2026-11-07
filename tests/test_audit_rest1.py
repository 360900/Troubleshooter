"""Checks for entries reviewed in the database audit (group rest1)."""
import subprocess

from tests.test_engine import DB, EngineCase
from troubleshooter import engine, patches


def plan_for(machine, entry_id):
    entry = DB.entries[entry_id]
    system = machine.system()
    fix = next(f for f in entry.fixes if system.in_scopes(f.systems))
    return patches.build_plan(entry.id, entry.title, fix.steps, fix.reboot, system)


def run_script(machine, script):
    """Runs a generated script against the fake root, with stub binaries only."""
    machine.install_stubs()
    env = {"PATH": f"{machine.stubs}:/usr/bin:/bin", "HOME": str(machine.home)}
    return subprocess.run(["bash", "-c", script], env=env, capture_output=True, text=True,
                          stdin=subprocess.DEVNULL, timeout=30)


class AmdGfxoffTests(EngineCase):
    def gfxoff(self, cmdline="root=x quiet"):
        m = self.machine()
        m.modules("amdgpu")
        m.write("/proc/cmdline", cmdline + "\n")
        return m, engine.diagnose_entry(DB.entries["amd-gfxoff-freeze"], m.system())

    def test_fix_keeps_the_default_mask_minus_gfxoff(self):
        # 0xffff7fff would also turn on Overdrive (0x4000), which taints the kernel.
        m, _ = self.gfxoff()
        m.write("/etc/kernel/cmdline", "root=x quiet\n")
        script = plan_for(m, "amd-gfxoff-freeze").script
        self.assertIn("amdgpu.ppfeaturemask=0xfff73fff", script)
        self.assertNotIn("0xffff7fff", script)
        self.assertEqual(0xfff7bfff & ~0x8000, 0xfff73fff)

    def test_any_mask_without_gfxoff_is_fixed(self):
        for mask in ("0xfff73fff", "0xffff7fff", "0xfffd7fff"):
            with self.subTest(mask=mask):
                self.assertEqual(self.gfxoff(f"root=x amdgpu.ppfeaturemask={mask}")[1].status, engine.FIXED)

    def test_own_mask_with_gfxoff_is_left_alone(self):
        d = self.gfxoff("root=x amdgpu.ppfeaturemask=0xffffffff")[1]
        self.assertEqual(d.status, engine.NOT_APPLICABLE)

    def test_no_mask_applies(self):
        self.assertEqual(self.gfxoff()[1].status, engine.APPLIES)


class AmdAbmTests(EngineCase):
    def abm(self, savings=None, packages=()):
        m = self.machine()
        m.modules("amdgpu")
        m.write("/sys/class/dmi/id/chassis_type", "10\n")
        m.packages = set(packages)
        if savings is not None:
            m.write("/sys/class/drm/card1-eDP-1/amdgpu/panel_power_savings", f"{savings}\n")
        return engine.diagnose_entry(DB.entries["amd-abm-washed-out"], m.system())

    def test_panel_power_savings_in_use_is_evidence(self):
        self.assertGreaterEqual(self.abm(3).confidence, engine.RECOMMEND_AT)

    def test_power_profiles_daemon_alone_is_not_evidence(self):
        # Its amdgpu_panel_power action is off by default since 0.30.
        d = self.abm(0, {"power-profiles-daemon"})
        self.assertFalse(d.found)
        self.assertLess(d.confidence, engine.RECOMMEND_AT)


class BluetoothTests(EngineCase):
    def test_timeouts_without_an_opcode_count(self):
        m = self.machine()
        m.journal("kernel: Bluetooth: hci0: command tx timeout\n")
        self.assertTrue(engine.evaluate(DB.entries["bluetooth-controller-stuck"], m.system()).found)

    def test_sleep_service_cant_hang_waiting_for_bluetoothd(self):
        # bluetoothctl waits for bluetoothd forever without --timeout, and a oneshot unit
        # has no start timeout, so sleep would never happen with bluetooth.service stopped.
        m = self.machine()
        execs = [line for line in plan_for(m, "bluetooth-wakes-from-sleep").script.splitlines()
                 if line.startswith("Exec")]
        self.assertEqual(len(execs), 2)
        for line in execs:
            self.assertIn("bluetoothctl --timeout", line)

    def wireplumber(self, entry_id, path, text):
        m = self.machine()
        m.packages = {"wireplumber", "bluez"}
        target = m.home / path[2:] if path.startswith("~/") else m.root / path.lstrip("/")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)
        return engine.diagnose_entry(DB.entries[entry_id], m.system()).status

    def test_wireplumber_settings_made_elsewhere_count(self):
        cases = [
            ("bluetooth-headset-autoswitch", "~/.local/state/wireplumber/sm-settings",
             "[sm-settings]\nbluetooth.autoswitch-to-headset-profile=false\n"),
            ("bluetooth-headset-autoswitch", "~/.config/wireplumber/wireplumber.conf.d/51-mine.conf",
             "wireplumber.settings = {\n  bluetooth.autoswitch-to-headset-profile = false\n}\n"),
            ("bluetooth-headset-hw-volume", "/etc/wireplumber/wireplumber.conf.d/80-bluez-properties.conf",
             "monitor.bluez.properties = {\n  bluez5.enable-hw-volume = false\n}\n"),
            ("bluetooth-a2dp-silent-avrcp", "~/.config/wireplumber/wireplumber.conf.d/60-bluez-dummy-avrcp.conf",
             "monitor.bluez.properties = {\n  bluez5.dummy-avrcp-player = true\n}\n"),
        ]
        for entry_id, path, text in cases:
            with self.subTest(entry=entry_id, path=path):
                self.assertEqual(self.wireplumber(entry_id, path, text), engine.FIXED)

    def test_commented_or_opposite_wireplumber_settings_dont_count(self):
        path = "/etc/wireplumber/wireplumber.conf.d/51-mine.conf"
        text = "wireplumber.settings = {\n  # bluetooth.autoswitch-to-headset-profile = false\n}\n"
        self.assertEqual(self.wireplumber("bluetooth-headset-autoswitch", path, text), engine.APPLIES)
        text = "monitor.bluez.properties = {\n  bluez5.enable-hw-volume = true\n}\n"
        self.assertEqual(self.wireplumber("bluetooth-headset-hw-volume", path, text), engine.APPLIES)

    def test_our_own_files_still_count(self):
        for entry_id in ("bluetooth-headset-autoswitch", "bluetooth-headset-hw-volume", "bluetooth-a2dp-silent-avrcp"):
            with self.subTest(entry=entry_id):
                m = self.machine()
                m.packages = {"wireplumber", "bluez"}
                result = run_script(m, plan_for(m, entry_id).script)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(engine.diagnose_entry(DB.entries[entry_id], m.system()).status, engine.FIXED)


class RateMirrorsTests(EngineCase):
    def mirrors(self, timer):
        m = self.machine()
        m.packages = {"cachyos-rate-mirrors"}
        m.outputs["services"] = {"cachyos-rate-mirrors.timer": timer}
        m.journal("systemd[1]: cachyos-rate-mirrors.service: Consumed 4.1s CPU time over 32.5s wall clock time.\n")
        return engine.diagnose_entry(DB.entries["cachyos-rate-mirrors-slow-boot"], m.system())

    def test_a_turned_off_timer_counts_as_fixed(self):
        self.assertEqual(self.mirrors("masked").status, engine.FIXED)
        self.assertEqual(self.mirrors("disabled").status, engine.FIXED)

    def test_a_long_run_with_the_timer_on_is_found(self):
        d = self.mirrors("enabled")
        self.assertEqual(d.status, engine.APPLIES)
        self.assertTrue(d.found)


class PrintingTests(EngineCase):
    def test_gutenprint_fix_backs_up_the_queues_it_rewrites(self):
        m = self.machine()
        m.packages = {"gutenprint", "cups"}
        plan = plan_for(m, "cups-gutenprint-ppd")
        self.assertIn("pacman -S --needed --noconfirm perl", plan.script)
        self.assertLess(plan.script.index('ts_backup "$f"'), plan.script.index("; cups-genppdupdate;"))
        self.assertIn("pacman -Rns --noconfirm perl", plan.undo_script)
        self.assertIn("systemctl try-restart cups.service", plan.undo_script)

    def test_gutenprint_fix_stops_when_the_update_fails(self):
        # With "a && b" bash's set -e ignores a failing a, and the script reported success.
        m = self.machine()
        m.packages = {"gutenprint", "cups", "perl"}
        script = plan_for(m, "cups-gutenprint-ppd").script
        script = script.replace("cups-genppdupdate;", "false;")
        result = run_script(m, script)
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("==> Done", result.stdout)

    def test_missing_ghostscript_alone_is_only_a_hint(self):
        m = self.machine()
        m.packages = {"cups"}
        # Driverless printers take PDF and don't need it.
        d = engine.evaluate(DB.entries["cups-ghostscript-missing"], m.system())
        self.assertLess(d.confidence, engine.RECOMMEND_AT)


class DiskErrorTests(EngineCase):
    def errors(self, line):
        m = self.machine()
        m.journal(line + "\n")
        return engine.evaluate(DB.entries["disk-io-errors"], m.system()).found

    def test_read_errors_are_found(self):
        self.assertTrue(self.errors("kernel: I/O error, dev sda, sector 123 op 0x0:(READ) flags 0x80700 "
                                    "phys_seg 1 prio class 0"))
        self.assertTrue(self.errors("kernel: I/O error, dev nvme0n1, sector 9 op 0x1:(WRITE) flags 0x0 "
                                    "phys_seg 1 prio class 0"))

    def test_unsupported_discard_is_not_a_failing_drive(self):
        self.assertFalse(self.errors("kernel: I/O error, dev sdb, sector 2048 op 0x3:(DISCARD) flags 0x800 "
                                     "phys_seg 1 prio class 0"))
        self.assertFalse(self.errors("kernel: I/O error, dev sdb, sector 0 op 0x9:(WRITE_ZEROES) flags 0x800 "
                                     "phys_seg 0 prio class 0"))
