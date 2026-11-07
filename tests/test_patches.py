"""Runs the generated root scripts for real, against a fake root and stub binaries."""
import subprocess
import unittest

from tests.fakes import ARCH, CACHY, DEBIAN, FEDORA, FakeMachine
from troubleshooter import engine, patches
from troubleshooter.database import load_database

DB = load_database()
GRUB = '# comment\nGRUB_TIMEOUT=5\nGRUB_CMDLINE_LINUX_DEFAULT="loglevel=3 quiet"\nGRUB_CMDLINE_LINUX=""\n'
LIMINE = 'ESP_PATH="/boot"\nKERNEL_CMDLINE[default]+="quiet rw"\n'


class PatchCase(unittest.TestCase):
    def machine(self, release=ARCH, fail=()):
        m = FakeMachine(release)
        self.addCleanup(m.close)
        m.install_stubs(fail)
        return m

    def run_script(self, m, script, uid=None):
        env = {"PATH": f"{m.stubs}:/usr/bin:/bin", "HOME": str(m.home)}
        if uid is not None:
            env["PKEXEC_UID"] = str(uid)
        return subprocess.run(["bash", "-c", script], env=env, capture_output=True, text=True,
                              stdin=subprocess.DEVNULL, timeout=30)

    def plan(self, m, entry_id):
        entry = DB.entries[entry_id]
        system = m.system()
        fix = next(f for f in entry.fixes if system.in_scopes(f.systems))
        return patches.build_plan(entry.id, entry.title, fix.steps, fix.reboot, system)

    def apply(self, m, plan):
        result = self.run_script(m, plan.script)
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        return result


class KernelParamTests(PatchCase):
    def sleep_machine(self, loader):
        m = self.machine()
        m.packages = {"nvidia-utils", loader}
        if loader == "grub":
            m.write("/etc/default/grub", GRUB)
        if loader == "limine":
            m.write("/etc/default/limine", LIMINE)
            m.write("/usr/bin/limine-update", "#!/bin/sh\n")
        return m

    def test_grub(self):
        m = self.sleep_machine("grub")
        self.apply(m, self.plan(m, "nvidia-sleep"))
        text = m.read("/etc/default/grub")
        self.assertIn('GRUB_CMDLINE_LINUX_DEFAULT="loglevel=3 quiet nvidia.NVreg_PreserveVideoMemoryAllocations=1 '
                      'nvidia.NVreg_TemporaryFilePath=/var/tmp"\n', text)
        self.assertIn('GRUB_CMDLINE_LINUX=""\n', text)
        self.assertIn("GRUB_TIMEOUT=5\n", text)
        run = m.commands_run()
        self.assertIn("grub-mkconfig -o /boot/grub/grub.cfg", run)
        self.assertIn("mkinitcpio -P", run)
        self.assertIn("systemctl enable nvidia-suspend.service nvidia-hibernate.service nvidia-resume.service", run)

    def test_grub_single_quotes_and_empty_value(self):
        for line, expected in (("GRUB_CMDLINE_LINUX_DEFAULT='quiet'", "GRUB_CMDLINE_LINUX_DEFAULT='quiet nvidia_drm.modeset=1 nvidia_drm.fbdev=1'"),
                               ('GRUB_CMDLINE_LINUX_DEFAULT=""', 'GRUB_CMDLINE_LINUX_DEFAULT="nvidia_drm.modeset=1 nvidia_drm.fbdev=1"')):
            m = self.machine()
            m.packages = {"grub"}
            m.write("/etc/default/grub", line + "\n")
            self.apply(m, self.plan(m, "nvidia-drm-modeset"))
            with self.subTest(line=line):
                self.assertEqual(m.read("/etc/default/grub"), expected + "\n")

    def test_grub_missing_line_gets_added(self):
        m = self.machine()
        m.packages = {"grub"}
        m.write("/etc/default/grub", "GRUB_TIMEOUT=5\n")
        self.apply(m, self.plan(m, "amd-psr-flicker"))
        self.assertIn('GRUB_CMDLINE_LINUX_DEFAULT="amdgpu.dcdebugmask=0x10"', m.read("/etc/default/grub"))

    def test_limine_entry_tool_uses_limine_update_not_bare_mkinitcpio(self):
        m = self.sleep_machine("limine")
        self.apply(m, self.plan(m, "nvidia-sleep"))
        self.assertIn('KERNEL_CMDLINE[default]+="nvidia.NVreg_PreserveVideoMemoryAllocations=1 '
                      'nvidia.NVreg_TemporaryFilePath=/var/tmp"\n', m.read("/etc/default/limine"))
        run = m.commands_run()
        self.assertEqual(run.count("limine-update "), 1, run)
        self.assertNotIn("mkinitcpio -P", run)

    def test_grub_config_is_left_alone_when_limine_booted(self):
        m = self.sleep_machine("limine")
        m.write("/etc/default/grub", GRUB)
        m.packages.add("grub")
        (m.root / "sys/firmware/efi/efivars").mkdir(parents=True)
        (m.root / f"sys/firmware/efi/efivars/LoaderInfo-x").write_bytes(b"\7\0\0\0" + "Limine 9".encode("utf-16-le"))
        self.apply(m, self.plan(m, "nvidia-sleep"))
        self.assertEqual(m.read("/etc/default/grub"), GRUB)

    def test_plain_limine_conf_at_runtime(self):
        m = self.machine()
        m.packages = {"limine"}
        conf = "timeout: 3\n/Arch\n  protocol: linux\n  cmdline: root=UUID=1 rw\n/Lts\n  kernel_cmdline: root=UUID=1\n"
        m.write("/boot/limine.conf", conf)
        self.apply(m, self.plan(m, "nvidia-drm-modeset"))
        text = m.read("/boot/limine.conf")
        self.assertIn("  cmdline: root=UUID=1 rw nvidia_drm.modeset=1 nvidia_drm.fbdev=1\n", text)
        self.assertIn("  kernel_cmdline: root=UUID=1 nvidia_drm.modeset=1 nvidia_drm.fbdev=1\n", text)
        self.assertIn("timeout: 3\n", text)

    def test_systemd_boot_entries(self):
        m = self.machine()
        m.write("/boot/loader/entries/arch.conf", "title Arch\nlinux /vmlinuz\noptions root=UUID=1 rw\n")
        self.apply(m, self.plan(m, "amd-psr-flicker"))
        self.assertIn("options root=UUID=1 rw amdgpu.dcdebugmask=0x10\n", m.read("/boot/loader/entries/arch.conf"))

    def test_sdboot_manage(self):
        m = self.machine()
        m.write("/etc/sdboot-manage.conf", 'LINUX_OPTIONS="zswap.enabled=0"\n')
        self.apply(m, self.plan(m, "amd-psr-flicker"))
        self.assertEqual(m.read("/etc/sdboot-manage.conf"), 'LINUX_OPTIONS="zswap.enabled=0 amdgpu.dcdebugmask=0x10"\n')
        self.assertIn("sdboot-manage gen", m.commands_run())

    def test_uki_drop_in_with_mkinitcpio(self):
        m = self.machine()
        m.packages = {"grub"}
        m.write("/etc/default/grub", GRUB)
        m.write("/etc/mkinitcpio.d/linux.preset", 'default_uki="/efi/x.efi"\n')
        self.apply(m, self.plan(m, "amd-psr-flicker"))
        self.assertEqual(m.read("/etc/cmdline.d/troubleshooter.conf"), "amdgpu.dcdebugmask=0x10\n")
        self.assertEqual(m.commands_run().count("mkinitcpio -P"), 1)

    def test_params_already_set_are_skipped(self):
        m = self.sleep_machine("grub")
        m.write("/proc/cmdline", "nvidia.NVreg_PreserveVideoMemoryAllocations=1 nvidia.NVreg_TemporaryFilePath=/var/tmp\n")
        plan = self.plan(m, "nvidia-sleep")
        self.assertNotIn("/etc/default/grub", plan.script)

    def test_running_twice_does_not_duplicate(self):
        m = self.machine()
        m.packages = {"grub"}
        m.write("/etc/default/grub", GRUB)
        plan = self.plan(m, "amd-psr-flicker")
        self.apply(m, plan)
        self.run_script(m, plan.script.replace(plan.run_id, plan.run_id + "b"))
        self.assertEqual(m.read("/etc/default/grub").count("amdgpu.dcdebugmask"), 1)


class UndoTests(PatchCase):
    def test_restores_edited_file_and_rebuilds_boot_files(self):
        m = self.machine()
        m.packages = {"grub"}
        m.write("/etc/default/grub", GRUB)
        plan = self.plan(m, "nvidia-sleep")
        self.apply(m, plan)
        self.assertNotEqual(m.read("/etc/default/grub"), GRUB)
        before = len(m.commands_run())
        result = self.run_script(m, plan.undo_script)
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertEqual(m.read("/etc/default/grub"), GRUB)
        undo_run = m.commands_run()[before:]
        self.assertIn("systemctl disable nvidia-suspend.service nvidia-hibernate.service nvidia-resume.service", undo_run)
        self.assertLess(undo_run.index("systemctl disable nvidia-suspend.service nvidia-hibernate.service nvidia-resume.service"),
                        undo_run.index("grub-mkconfig -o /boot/grub/grub.cfg"))

    def test_removes_files_the_fix_created(self):
        m = self.machine()
        plan = self.plan(m, "games-max-map-count")
        self.apply(m, plan)
        conf = "/etc/sysctl.d/90-troubleshooter-games-max-map-count.conf"
        self.assertEqual(m.read(conf), "vm.max_map_count = 1048576\n")
        self.assertEqual(self.run_script(m, plan.undo_script).returncode, 0)
        self.assertFalse(m.exists(conf))

    def test_backup_keeps_the_original_when_a_file_is_edited_twice(self):
        m = self.machine()
        m.write("/etc/pacman.conf", "#[multilib]\n#Include = /etc/pacman.d/mirrorlist\n")
        plan = patches.build_plan("x", "x", [
            {"op": "replace", "path": "/etc/pacman.conf", "pattern": "#", "replacement": "A", "says": "one"},
            {"op": "replace", "path": "/etc/pacman.conf", "pattern": "A", "replacement": "B", "says": "two"},
        ], False, m.system())
        self.apply(m, plan)
        self.assertEqual(self.run_script(m, plan.undo_script).returncode, 0)
        self.assertEqual(m.read("/etc/pacman.conf"), "#[multilib]\n#Include = /etc/pacman.d/mirrorlist\n")

    def test_undo_reports_failure_but_keeps_going(self):
        m = self.machine()
        m.packages = {"grub"}
        m.write("/etc/default/grub", GRUB)
        plan = self.plan(m, "nvidia-sleep")
        self.apply(m, plan)
        for stub in ("systemctl",):
            (m.stubs / stub).write_text("#!/bin/sh\nexit 1\n")
        result = self.run_script(m, plan.undo_script)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(m.read("/etc/default/grub"), GRUB)
        self.assertIn("mkinitcpio -P", m.commands_run())

    def test_undo_disables_a_new_service_before_deleting_its_unit_file(self):
        m = self.machine()
        steps = [{"op": "write_file", "path": "/etc/systemd/system/ts-test.service", "content": "[Unit]\n"},
                 {"op": "service", "names": ["ts-test.service"], "now": True}]
        plan = patches.build_plan("ts-test", "Test", steps, False, m.system())
        self.apply(m, plan)
        unit = m.root / "etc/systemd/system/ts-test.service"
        # Like the real systemctl: disabling a unit whose file is gone fails.
        (m.stubs / "systemctl").write_text(f'#!/bin/sh\n[ "$1" != disable ] || [ -e {unit} ]\n')
        result = self.run_script(m, plan.undo_script)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse(unit.exists())

    def test_undo_refuses_when_backup_is_gone(self):
        m = self.machine()
        plan = self.plan(m, "games-max-map-count")
        self.assertEqual(self.run_script(m, plan.undo_script).returncode, 1)


class FailureTests(PatchCase):
    def test_script_stops_at_first_failure(self):
        m = self.machine(fail=("systemctl",))
        m.packages = {"grub"}
        m.write("/etc/default/grub", GRUB)
        result = self.run_script(m, self.plan(m, "nvidia-sleep").script)
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("mkinitcpio -P", m.commands_run())

    def test_script_never_reads_stdin(self):
        """Limine's mkinitcpio wrapper prompts; a script piped on stdin would feed it."""
        m = self.machine()
        plan = self.plan(m, "games-max-map-count")
        self.assertNotIn("read -r", plan.script.split("ts_backup()")[0])
        self.assertEqual(self.run_script(m, plan.script).returncode, 0)


class DriverSwapTests(PatchCase):
    def legacy_machine(self, repos=()):
        m = self.machine(CACHY)
        m.pci("0000:01:00.0", "10de", "1c03", "030000")
        m.packages = {"nvidia-open-dkms", "nvidia-utils", "lib32-nvidia-utils", "linux-cachyos-nvidia-open", "linux-cachyos"}
        m.write("/usr/lib/modules/7.0/pkgbase", "linux-cachyos\n")
        m.outputs["repos"] = set(repos)
        return m

    def test_from_repos(self):
        m = self.legacy_machine(["nvidia-580xx-dkms", "nvidia-580xx-utils", "lib32-nvidia-580xx-utils"])
        self.apply(m, self.plan(m, "nvidia-open-on-legacy"))
        run = m.commands_run()
        removed = next(i for i, c in enumerate(run) if c.startswith("pacman -Rdd"))
        installed = next(i for i, c in enumerate(run) if "nvidia-580xx-dkms" in c and c.startswith("pacman -S"))
        self.assertLess(removed, installed)
        self.assertIn("linux-cachyos-nvidia-open", run[removed])
        self.assertIn("linux-cachyos-headers", run[installed])
        self.assertIn("lib32-nvidia-580xx-utils", run[installed])
        self.assertFalse(any("makepkg" in c for c in run))

    def test_from_aur_builds_before_touching_the_working_driver(self):
        m = self.legacy_machine()
        plan = self.plan(m, "nvidia-open-on-legacy")
        self.assertIn("AUR", plan.summary[0])
        result = self.run_script(m, plan.script, uid=1000)
        run = m.commands_run()
        built = next(i for i, c in enumerate(run) if c.startswith("makepkg -f"))
        removed = next(i for i, c in enumerate(run) if c.startswith("pacman -Rdd"))
        self.assertLess(built, removed)
        self.assertTrue(any("pacman -S --needed --noconfirm --asdeps patchelf" == c for c in run), run)
        self.assertFalse(any("other" in c and c.startswith("pacman -S") for c in run), run)
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)

    def test_failed_build_leaves_the_driver_installed(self):
        m = self.legacy_machine()
        plan = self.plan(m, "nvidia-open-on-legacy")
        (m.stubs / "makepkg").write_text('#!/bin/sh\ncase "$*" in *printsrcinfo*) ;; *) exit 1 ;; esac\n')
        result = self.run_script(m, plan.script, uid=1000)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(any(c.startswith("pacman -Rdd") for c in m.commands_run()))

    def test_undo_puts_the_old_driver_back(self):
        m = self.legacy_machine(["nvidia-580xx-dkms", "nvidia-580xx-utils", "lib32-nvidia-580xx-utils"])
        plan = self.plan(m, "nvidia-open-on-legacy")
        self.apply(m, plan)
        before = len(m.commands_run())
        self.assertEqual(self.run_script(m, plan.undo_script).returncode, 0)
        run = m.commands_run()[before:]
        self.assertTrue(run[0].startswith("pacman -Rdd") and "nvidia-580xx-dkms" in run[0], run)
        self.assertTrue(run[1].startswith("pacman -S") and "nvidia-open-dkms" in run[1], run)


class InitramfsTests(PatchCase):
    """Arch-based systems build boot images with mkinitcpio or dracut, sometimes through a wrapper."""
    STEPS = [{"op": "initramfs"}]

    def rebuild(self, m):
        plan = patches.build_plan("ts-test", "Test", self.STEPS, True, m.system())
        self.apply(m, plan)
        return plan, m.commands_run()

    def dracut_machine(self, *packages):
        m = self.machine()
        m.packages = {"grub", *packages}
        for kver, pkgbase in (("7.0.1-arch1-1", "linux"), ("6.18.4-1-lts", "linux-lts")):
            m.write(f"/usr/lib/modules/{kver}/pkgbase", pkgbase + "\n")
        # Stands in for the dracut package's pacman hook script: logs the targets it's given.
        m.write(patches.DRACUT_HOOK, f'#!/bin/sh\nwhile read -r t; do echo "dracut-hook $t" >> "{m.log}"; done\n',
                mode=0o755)
        return m

    def test_mkinitcpio_is_the_default(self):
        _, run = self.rebuild(self.dracut_machine("mkinitcpio", "dracut"))
        self.assertIn("mkinitcpio -P", run)
        self.assertFalse([c for c in run if c.startswith("dracut")], run)

    def test_endeavouros_systemd_boot_uses_reinstall_kernels(self):
        plan, run = self.rebuild(self.dracut_machine("dracut", "kernel-install-for-dracut"))
        self.assertEqual(plan.summary, ["Rebuild the boot images (reinstall-kernels)"])
        self.assertIn("reinstall-kernels ", run)
        self.assertNotIn("mkinitcpio -P", run)

    def test_endeavouros_grub_uses_dracut_rebuild(self):
        _, run = self.rebuild(self.dracut_machine("dracut", "eos-dracut"))
        self.assertIn("dracut-rebuild ", run)
        self.assertNotIn("mkinitcpio -P", run)

    def test_plain_dracut_runs_its_pacman_hook_for_every_kernel(self):
        plan, run = self.rebuild(self.dracut_machine("dracut"))
        self.assertEqual(plan.summary, ["Rebuild the boot images (dracut)"])
        self.assertEqual(sorted(c for c in run if c.startswith("dracut-hook")),
                         ["dracut-hook usr/lib/modules/6.18.4-1-lts/pkgbase",
                          "dracut-hook usr/lib/modules/7.0.1-arch1-1/pkgbase"])
        self.assertNotIn("mkinitcpio -P", run)

    def test_dracut_when_mkinitcpio_hook_is_masked(self):
        m = self.dracut_machine("mkinitcpio", "dracut")
        m.write("/etc/pacman.d/hooks/90-mkinitcpio-install.hook", "")
        _, run = self.rebuild(m)
        self.assertTrue([c for c in run if c.startswith("dracut-hook")], run)
        self.assertNotIn("mkinitcpio -P", run)

    def test_masked_dracut_hook_is_reported_not_guessed(self):
        m = self.dracut_machine("dracut")
        m.write("/etc/pacman.d/hooks/90-dracut-install.hook", "")
        with self.assertRaises(patches.PlanError):
            patches.build_plan("ts-test", "Test", self.STEPS, True, m.system())


class OtherFixTests(PatchCase):
    def test_multilib_uncomments_only_its_own_block(self):
        m = self.machine()
        conf = ("[core]\nInclude = /etc/pacman.d/mirrorlist\n\n#[multilib-testing]\n#Include = /etc/pacman.d/mirrorlist\n\n"
                "#[multilib]\n#Include = /etc/pacman.d/mirrorlist\n")
        m.write("/etc/pacman.conf", conf)
        self.apply(m, self.plan(m, "multilib-disabled"))
        self.assertEqual(m.read("/etc/pacman.conf"),
                         "[core]\nInclude = /etc/pacman.d/mirrorlist\n\n#[multilib-testing]\n#Include = /etc/pacman.d/mirrorlist\n\n"
                         "[multilib]\nInclude = /etc/pacman.d/mirrorlist\n")

    def test_broadcom_writes_blacklist_and_installs_headers(self):
        m = self.machine()
        m.write("/usr/lib/modules/6.1/pkgbase", "linux\n")
        self.apply(m, self.plan(m, "wifi-broadcom-wl"))
        self.assertIn("blacklist b43\n", m.read("/etc/modprobe.d/troubleshooter-wifi-broadcom-wl-blacklist.conf"))
        self.assertIn("pacman -S --needed --noconfirm broadcom-wl-dkms linux-headers", m.commands_run())

    def test_module_options(self):
        m = self.machine()
        self.apply(m, self.plan(m, "wifi-realtek-rtw89"))
        self.assertIn("options rtw89_pci disable_aspm_l1=Y disable_aspm_l1ss=Y\n",
                      m.read("/etc/modprobe.d/troubleshooter-wifi-realtek-rtw89-rtw89_pci.conf"))

    def test_debian_and_fedora_use_their_package_manager_and_initramfs(self):
        for release, install in ((DEBIAN, "apt-get install -y firmware-sof-signed"), (FEDORA, "dnf install -y alsa-sof-firmware")):
            m = self.machine(release)
            self.apply(m, self.plan(m, "sof-firmware-missing"))
            with self.subTest(release=release):
                self.assertIn(install, m.commands_run())

    def test_nothing_left_to_do_is_an_error_not_an_empty_script(self):
        m = self.machine()
        m.packages = {"vulkan-radeon"}
        with self.assertRaises(patches.PlanError):
            self.plan(m, "amd-vulkan-missing")

    def test_every_fix_compiles_and_parses_on_every_family(self):
        for release in (ARCH, CACHY, DEBIAN, FEDORA):
            m = self.machine(release)
            for entry in DB.entries.values():
                for fix in entry.fixes:
                    # Whatever the fix removes or disables is present, so those steps have something to do.
                    m.packages = {"grub"} | {name for step in fix.steps if step["op"] == "remove"
                                             for name in step["packages"] if "*" not in name}
                    m.outputs["services"] = {name: "enabled" for step in fix.steps
                                             if step["op"] == "service" and step.get("action") == "disable"
                                             for name in step["names"]}
                    system = m.system()
                    if not system.in_scopes(fix.systems):
                        continue
                    plan = patches.build_plan(entry.id, entry.title, fix.steps, fix.reboot, system)
                    self.assertTrue(plan.summary)
                    for script in (plan.script, plan.undo_script):
                        check = subprocess.run(["bash", "-n"], input=script, text=True, capture_output=True)
                        with self.subTest(entry=entry.id, release=release):
                            self.assertEqual(check.returncode, 0, check.stderr)
