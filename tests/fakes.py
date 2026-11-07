"""A throwaway machine: a fake root directory, fake command output, stub binaries."""
import os
import stat
import tempfile
from pathlib import Path

from troubleshooter.system import Result, System

ARCH = 'NAME="Arch Linux"\nID=arch\n'
CACHY = 'NAME="CachyOS"\nID=cachyos\nID_LIKE=arch\n'
DEBIAN = 'NAME="Debian"\nID=debian\n'
UBUNTU = 'NAME="Ubuntu"\nID=ubuntu\nID_LIKE=debian\n'
FEDORA = 'NAME="Fedora"\nID=fedora\n'
NOBARA = 'NAME="Nobara"\nID=nobara\nID_LIKE="rhel centos fedora"\n'

# Every command a generated script may call, so nothing real ever runs.
STUBS = ("pacman", "mkinitcpio", "limine-update", "grub-mkconfig", "update-grub", "grubby", "sdboot-manage",
         "systemctl", "sysctl", "dkms", "update-initramfs", "dracut", "dracut-rebuild", "reinstall-kernels", "apt-get", "dnf", "runuser", "git", "makepkg")


class FakeMachine:
    def __init__(self, os_release=ARCH):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name) / "root"
        self.home = Path(self._tmp.name) / "home"
        self.log = Path(self._tmp.name) / "commands.log"
        self.stubs = Path(self._tmp.name) / "stubs"
        self.root.mkdir()
        self.home.mkdir()
        self.stubs.mkdir()
        self.outputs = {}
        self.packages = set()
        self.write("/etc/os-release", os_release)

    def close(self):
        self._tmp.cleanup()

    def write(self, path, text, mode=None):
        target = self.root / path.lstrip("/")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)
        if mode:
            target.chmod(mode)
        return target

    def read(self, path):
        return (self.root / path.lstrip("/")).read_text()

    def exists(self, path):
        return (self.root / path.lstrip("/")).exists()

    def pci(self, slot, vendor, device, cls, driver=None):
        base = self.root / "sys/bus/pci/devices" / slot
        base.mkdir(parents=True)
        (base / "vendor").write_text(f"0x{vendor}\n")
        (base / "device").write_text(f"0x{device}\n")
        (base / "class").write_text(f"0x{cls}\n")
        if driver:
            os.symlink(f"../../../bus/pci/drivers/{driver}", base / "driver")

    def modules(self, *names):
        self.write("/proc/modules", "".join(f"{n} 1 0 - Live 0x0\n" for n in names))

    def journal(self, text, window=False):
        self.outputs["journal-window" if window else "journal"] = text

    def _commands(self, argv):
        tool = argv[0]
        if tool == "journalctl":
            if any(a.startswith("--after-cursor") for a in argv):
                return Result(0, self.outputs.get("journal-window", ""))
            if "--show-cursor" in argv:
                return Result(0, "-- cursor: s=fake;i=1\n")
            if self.outputs.get("journal", "") is None:
                return Result(127, "")
            return Result(0, self.outputs.get("journal", ""))
        if argv[:2] == ["pacman", "-Qq"]:
            return Result(0, "\n".join(sorted(self.packages)) + "\n")
        if argv[:2] == ["pacman", "-Si"]:
            return Result(0 if argv[2] in self.outputs.get("repos", ()) else 1, "")
        if tool == "systemctl":
            state = self.outputs.get("services", {}).get(argv[2], "disabled")
            return Result(0 if state == "enabled" else 1, state + "\n")
        if tuple(argv) in self.outputs.get("commands", {}):
            out = self.outputs["commands"][tuple(argv)]
            return out if isinstance(out, Result) else Result(0, out)
        if tool == "dpkg-query" or tool == "rpm":
            return Result(0, "\n".join(sorted(self.packages)) + "\n")
        return None

    def system(self):
        system = System(root=self.root, home=self.home, env={}, commands=self._commands, user="tester")
        system.script_path = f"{self.stubs}:/usr/bin:/bin"
        return system

    def install_stubs(self, fail=()):
        for name in STUBS:
            stub = self.stubs / name
            body = f'#!/bin/sh\necho "{name} $*" >> "{self.log}"\n'
            if name in fail:
                body += "exit 1\n"
            if name == "runuser":  # runuser -u user -- cmd...
                body += 'shift 3\nexec "$@"\n'
            if name == "makepkg":
                body += '''case "$*" in
  *printsrcinfo*) printf '\\tmakedepends = patchelf\\n\\tdepends = other=1.0\\n' ;;
  *) touch nvidia-580xx-dkms-1.0-1-x86_64.pkg.tar.zst nvidia-580xx-utils-1.0-1-x86_64.pkg.tar.zst lib32-nvidia-580xx-utils-1.0-1-x86_64.pkg.tar.zst ;;
esac
'''
            if name == "git":  # git clone --depth 1 url dir
                body += 'mkdir -p "$5"\n'
            stub.write_text(body)
            stub.chmod(stub.stat().st_mode | stat.S_IXUSR)

    def commands_run(self):
        return self.log.read_text().splitlines() if self.log.exists() else []
