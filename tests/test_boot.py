import struct
import unittest

from tests.fakes import ARCH, FEDORA, FakeMachine
from troubleshooter import boot
from troubleshooter.boot import EFI_GLOBAL


def efi_option(description, path):
    desc = (description + "\0").encode("utf-16-le")
    return struct.pack("<IIH", 7, 1, len(path)) + desc + path.encode("utf-16-le")


class BootTests(unittest.TestCase):
    def machine(self, release=ARCH):
        m = FakeMachine(release)
        self.addCleanup(m.close)
        return m

    def efi(self, m, description, path):
        vars_dir = "/sys/firmware/efi/efivars"
        (m.root / vars_dir.lstrip("/")).mkdir(parents=True, exist_ok=True)
        (m.root / f"{vars_dir[1:]}/BootCurrent-{EFI_GLOBAL}").write_bytes(struct.pack("<IH", 7, 3))
        (m.root / f"{vars_dir[1:]}/Boot0003-{EFI_GLOBAL}").write_bytes(efi_option(description, path))

    def test_firmware_entry_decides_when_grub_and_limine_coexist(self):
        m = self.machine()
        m.write("/etc/default/grub", 'GRUB_CMDLINE_LINUX_DEFAULT="quiet"\n')
        m.write("/etc/default/limine", 'KERNEL_CMDLINE[default]+="quiet"\n')
        m.packages = {"grub", "limine"}
        self.efi(m, "Limine", r"\EFI\limine\limine_x64.efi")
        self.assertEqual(boot.loaders(m.system()), ["limine"])

    def test_generic_boot_entry_falls_back_to_what_is_installed(self):
        m = self.machine()
        m.write("/etc/default/grub", 'GRUB_CMDLINE_LINUX_DEFAULT="quiet"\n')
        m.packages = {"grub"}
        self.efi(m, "UEFI OS", r"\EFI\BOOT\BOOTX64.EFI")
        self.assertEqual(boot.loaders(m.system()), ["grub"])

    def test_loader_named_only_in_path(self):
        m = self.machine()
        self.efi(m, "Linux", r"\EFI\systemd\systemd-bootx64.efi")
        self.assertEqual(boot.loaders(m.system()), ["systemd-boot"])

    def test_param_found_in_cmdline(self):
        m = self.machine()
        m.write("/proc/cmdline", "root=x nvidia_drm.modeset=1\n")
        self.assertTrue(boot.param_locations(m.system(), "nvidia_drm.modeset=1"))
        self.assertFalse(boot.param_locations(m.system(), "nvidia_drm.modeset=0"))

    def test_param_found_with_dash_spelling(self):
        m = self.machine()
        m.write("/proc/cmdline", "nvidia-drm.modeset=1\n")
        self.assertTrue(boot.param_locations(m.system(), "nvidia_drm.modeset=1"))

    def test_param_found_in_each_config(self):
        param = "nvidia.NVreg_TemporaryFilePath=/var/tmp"
        for path, text in {
            "/etc/default/grub": f'GRUB_CMDLINE_LINUX_DEFAULT="quiet {param}"\n',
            "/etc/default/limine": f'KERNEL_CMDLINE[default]+="{param}"\n',
            "/etc/kernel/cmdline": f"quiet {param}\n",
            "/etc/cmdline.d/x.conf": f"{param}\n",
        }.items():
            m = self.machine()
            m.write(path, text)
            with self.subTest(path=path):
                self.assertEqual(boot.param_locations(m.system(), param), [str(m.root / path[1:])])

    def test_param_found_as_module_option(self):
        m = self.machine()
        m.write("/etc/modprobe.d/nv.conf", "options nvidia NVreg_TemporaryFilePath=/var/tmp\n")
        self.assertTrue(boot.param_locations(m.system(), "nvidia.NVreg_TemporaryFilePath=/var/tmp"))

    def test_comments_do_not_count(self):
        m = self.machine()
        m.write("/etc/default/grub", '#GRUB_CMDLINE_LINUX_DEFAULT="nvidia_drm.modeset=1"\n')
        m.write("/etc/modprobe.d/nv.conf", "#options nvidia_drm modeset=1\n")
        self.assertFalse(boot.param_locations(m.system(), "nvidia_drm.modeset=1"))

    def test_uki_preset_adds_a_target(self):
        m = self.machine()
        m.packages = {"grub"}
        m.write("/etc/default/grub", "x\n")
        m.write("/etc/mkinitcpio.d/linux.preset", "default_uki=\"/efi/linux.efi\"\n")
        names = [t.name for t in boot.targets(m.system(), ["a=1"])]
        self.assertEqual(names, ["GRUB", "Unified kernel image"])

    def test_commented_uki_line_does_not(self):
        m = self.machine()
        m.write("/etc/mkinitcpio.d/linux.preset", "#default_uki=\"/efi/linux.efi\"\n")
        self.assertFalse(boot.uses_uki(m.system()))

    def test_fedora_uses_grubby(self):
        m = self.machine(FEDORA)
        m.write("/etc/default/grub", "x\n")
        target = boot.targets(m.system(), ["a=1"])[0]
        self.assertIn("grubby", target.lines[0])
