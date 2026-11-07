import unittest

from tests.test_engine import DB, EngineCase
from troubleshooter import engine


class AuditCase(EngineCase):
    def evaluate(self, entry_id, machine, env=None):
        system = machine.system()
        system.env = env or {}
        return engine.evaluate(DB.entries[entry_id], system)


class SynapticsTests(AuditCase):
    def test_x11_session_counts(self):
        m = self.machine()
        m.packages = {"xf86-input-synaptics"}
        d = self.evaluate("input-synaptics-driver", m, {"XDG_SESSION_TYPE": "x11"})
        self.assertEqual([f.signal.says for f in d.found], ["This session runs on X11, where the Synaptics driver applies"])
        self.assertFalse(self.evaluate("input-synaptics-driver", m, {"XDG_SESSION_TYPE": "wayland"}).found)


class NetworkingDisabledTests(AuditCase):
    def nm(self, state):
        m = self.machine()
        m.packages = {"networkmanager"}
        m.outputs["commands"] = {("nmcli", "networking"): state}
        return self.evaluate("nm-networking-disabled", m)

    def test_asks_networkmanager_because_its_state_file_is_root_only(self):
        d = self.nm("disabled\n")
        self.assertEqual(d.status, engine.APPLIES)
        self.assertTrue(d.recommended)
        self.assertEqual(self.nm("enabled\n").status, engine.FIXED)


class HibernateTests(AuditCase):
    def test_no_saved_resume_target_is_nothing_to_clear(self):
        m = self.machine()
        m.write("/sys/power/resume", "0:0\n")
        self.assertEqual(self.evaluate("hibernate-stale-resume-device", m).status, engine.FIXED)
        m.write("/sys/power/resume", "259:2\n")
        self.assertEqual(self.evaluate("hibernate-stale-resume-device", m).status, engine.APPLIES)

    def test_a_failed_suspend_is_not_blamed_on_swap(self):
        m = self.machine()
        m.write("/proc/swaps", "Filename Type Size Used Priority\n")
        m.journal("systemd-sleep[812]: Failed to put system to sleep. System resumed again: Device or resource busy\n")
        self.assertFalse(self.evaluate("hibernate-not-enough-swap", m).found)
        m.journal("systemd-sleep[812]: Failed to put system to sleep. System resumed again: No space left on device\n")
        self.assertTrue(self.evaluate("hibernate-not-enough-swap", m).found)

    def test_hibernate_mode_set_in_the_main_config_counts(self):
        m = self.machine()
        m.write("/sys/power/disk", "[platform] shutdown reboot suspend\n")
        m.write("/etc/systemd/sleep.conf", "[Sleep]\nHibernateMode=shutdown\n")
        self.assertEqual(self.evaluate("hibernate-no-poweroff", m).status, engine.FIXED)


class EarlyoomTests(AuditCase):
    def test_a_cgroup_hitting_its_own_limit_is_not_memory_running_out(self):
        m = self.machine()
        m.journal("kernel: oom-kill:constraint=CONSTRAINT_MEMCG,nodemask=(null),cpuset=/,mems_allowed=0\n"
                  "kernel: Memory cgroup out of memory: Killed process 4242 (node) total-vm:1kB\n")
        self.assertFalse(self.evaluate("memory-freeze-earlyoom", m).found)
        m.journal("kernel: oom-kill:constraint=CONSTRAINT_NONE,nodemask=(null),cpuset=/,mems_allowed=0\n"
                  "kernel: Out of memory: Killed process 4242 (firefox) total-vm:1kB\n")
        self.assertTrue(self.evaluate("memory-freeze-earlyoom", m).found)


class IntelPsrTests(AuditCase):
    def test_psr_idle_timeout_is_seen(self):
        m = self.machine()
        m.modules("i915")
        m.journal("kernel: i915 0000:00:02.0: [drm] *ERROR* Timed out waiting PSR idle state\n")
        self.assertTrue(self.evaluate("intel-psr-flicker", m).found)

    def test_psr_off_for_the_xe_driver_counts_on_xe(self):
        m = self.machine()
        m.modules("xe")
        m.write("/proc/cmdline", "root=/dev/sda1 xe.enable_psr=0\n")
        self.assertEqual(self.evaluate("intel-psr-flicker", m).status, engine.FIXED)
        m.modules("i915")
        self.assertEqual(self.evaluate("intel-psr-flicker", m).status, engine.APPLIES)


class IntelArcRebarTests(AuditCase):
    def test_small_bar_from_xe_is_evidence(self):
        m = self.machine()
        m.pci("0000:03:00.0", "8086", "e20b", "030000", "xe")  # Arc B580
        self.assertFalse(self.evaluate("intel-arc-rebar", m).found)
        m.journal("kernel: xe 0000:03:00.0: [drm] Small BAR device\n")
        self.assertTrue(self.evaluate("intel-arc-rebar", m).recommended)


class GpuBlacklistTests(AuditCase):
    def test_blacklisted_on_the_kernel_command_line(self):
        m = self.machine()
        m.pci("0000:00:02.0", "8086", "a7a0", "030000")
        m.modules("nvidia")
        m.write("/proc/cmdline", "root=/dev/sda1 module_blacklist=nouveau,i915 quiet\n")
        d = self.evaluate("gpu-driver-blacklisted", m)
        self.assertEqual(len(d.found), 1)
        m.write("/proc/cmdline", "root=/dev/sda1 module_blacklist=nouveau quiet\n")
        self.assertFalse(self.evaluate("gpu-driver-blacklisted", m).found)


class GrubWindowsTests(AuditCase):
    def grub(self, cfg="menuentry 'Arch Linux' --class arch {\n}\n"):
        m = self.machine()
        m.packages = {"grub"}
        m.write("/etc/default/grub", "GRUB_TIMEOUT=5\n#GRUB_DISABLE_OS_PROBER=false\n")
        m.write("/boot/grub/grub.cfg", cfg)
        m.outputs["commands"] = {("efibootmgr",): "BootCurrent: 0001\nBoot0000* Windows Boot Manager\tHD(1,GPT,x)/\\EFI\\Microsoft\\Boot\\bootmgfw.efi\nBoot0001* GRUB\tHD(1,GPT,x)/\\EFI\\GRUB\\grubx64.efi\n"}
        return self.evaluate("grub-windows-missing", m)

    def test_windows_found_in_firmware_entries_when_the_esp_is_root_only(self):
        self.assertEqual(self.grub().status, engine.APPLIES)

    def test_a_windows_entry_in_the_menu_is_fixed_however_it_got_there(self):
        cfg = "menuentry 'Windows Boot Manager (on /dev/nvme0n1p1)' --class windows --class os {\n}\n"
        self.assertEqual(self.grub(cfg).status, engine.FIXED)


class PrinterTests(AuditCase):
    def test_socket_activated_cups_is_fixed(self):
        m = self.machine()
        m.packages = {"gnome-control-center", "cups", "system-config-printer"}
        m.outputs["services"] = {"cups.socket": "enabled"}
        d = self.evaluate("gnome-printers-panel", m, {"XDG_CURRENT_DESKTOP": "GNOME"})
        self.assertEqual(d.status, engine.FIXED)


class ShaderCacheTests(AuditCase):
    def test_limit_set_in_the_session_environment_is_fixed(self):
        m = self.machine()
        d = self.evaluate("games-shader-cache-small", m, {"MESA_SHADER_CACHE_MAX_SIZE": "10G"})
        self.assertEqual(d.status, engine.FIXED)


if __name__ == "__main__":
    unittest.main()
