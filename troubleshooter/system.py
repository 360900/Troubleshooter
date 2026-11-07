"""Read-only view of the machine: files, commands, distro, packages, hardware.

Everything goes through one System object, so tests can point it at a fake root
and fake commands. Results of commands are cached for the life of the object;
make a new System for a new scan.
"""
from __future__ import annotations

import fnmatch
import glob
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

SCOPES = {
    "any": "Any Linux",
    "arch-only": "Arch only",
    "arch-and-based": "Arch and Arch-based",
    "debian-only": "Debian only",
    "debian-and-based": "Debian and Debian-based",
    "fedora-only": "Fedora only",
    "fedora-and-based": "Fedora and Fedora-based",
}
FAMILIES = ("arch", "debian", "fedora")


@dataclass(frozen=True)
class Result:
    code: int
    out: str

    @property
    def ok(self) -> bool:
        return self.code == 0

    @property
    def available(self) -> bool:
        """False when the command is missing (127) or timed out or couldn't start (124)."""
        return self.code not in (124, 127)


@dataclass(frozen=True)
class PciDevice:
    slot: str
    vendor: str
    device: str
    cls: str
    driver: str | None
    name: str

    def __str__(self) -> str:
        return self.name or f"{self.vendor}:{self.device}"


class System:
    def __init__(self, root="/", home=None, env=None, commands=None):
        self.root = Path(root)
        self.home = Path(home or Path.home())
        self.env = dict(os.environ if env is None else env)
        self._commands = commands
        self._cache: dict[tuple, Result] = {}
        self._os: dict[str, str] | None = None
        self._packages: set[str] | None = None
        self._packages_known = False
        self._pci: list[PciDevice] | None = None
        self.cursor: str | None = None
        self.script_path = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"

    # Files

    def path(self, p: str) -> Path:
        if p.startswith("~/"):
            return self.home / p[2:]
        return self.root / p.lstrip("/")

    def read(self, p: str | Path) -> str | None:
        target = p if isinstance(p, Path) else self.path(p)
        try:
            return target.read_text(errors="replace")
        except OSError:
            return None

    def read_bytes(self, p: str) -> bytes | None:
        try:
            return self.path(p).read_bytes()
        except OSError:
            return None

    def exists(self, p: str) -> bool:
        try:
            return self.path(p).exists()
        except OSError:
            return False

    def glob(self, pattern: str) -> list[Path]:
        return sorted(Path(m) for m in glob.glob(str(self.path(pattern))))

    # Commands

    def run(self, *argv: str, timeout: float = 15) -> Result:
        key = tuple(argv)
        if key not in self._cache:
            self._cache[key] = self._exec(list(argv), timeout)
        return self._cache[key]

    def _exec(self, argv: list[str], timeout: float) -> Result:
        if self._commands is not None:
            return self._commands(argv) or Result(127, "")
        try:
            p = subprocess.run(
                argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, errors="replace", timeout=timeout, env={**os.environ, "LC_ALL": "C"},
            )
            return Result(p.returncode, p.stdout)
        except FileNotFoundError:
            return Result(127, "")
        except (subprocess.TimeoutExpired, OSError):
            return Result(124, "")

    # Distro

    @property
    def os(self) -> dict[str, str]:
        if self._os is None:
            text = self.read("/etc/os-release") or self.read("/usr/lib/os-release") or ""
            self._os = {}
            for line in text.splitlines():
                if "=" in line and not line.lstrip().startswith("#"):
                    key, value = line.split("=", 1)
                    self._os[key.strip()] = value.strip().strip("\"'")
        return self._os

    @property
    def os_name(self) -> str:
        return self.os.get("PRETTY_NAME") or self.os.get("NAME") or "Unknown Linux"

    @property
    def family(self) -> str | None:
        ids = [self.os.get("ID", "").lower(), *self.os.get("ID_LIKE", "").lower().split()]
        for candidate in ids:
            if candidate in FAMILIES:
                return candidate
            if candidate in ("ubuntu", "linuxmint"):
                return "debian"
            if candidate in ("rhel", "centos"):
                return "fedora"
        return None

    def in_scope(self, scope: str) -> bool:
        if scope == "any":
            return True
        family, _, kind = scope.partition("-")
        if kind == "only":
            return self.os.get("ID", "").lower() == family
        return self.family == family

    def in_scopes(self, scopes: list[str]) -> bool:
        return any(self.in_scope(s) for s in scopes)

    # Packages

    @property
    def packages(self) -> set[str]:
        if self._packages is None:
            result = {
                "arch": lambda: self.run("pacman", "-Qq"),
                "debian": lambda: self.run("dpkg-query", "-W", "-f", "${Package}\n"),
                "fedora": lambda: self.run("rpm", "-qa", "--qf", "%{NAME}\n"),
            }.get(self.family, lambda: Result(127, ""))()
            self._packages = set(result.out.split()) if result.ok else set()
            self._packages_known = result.ok
        return self._packages

    @property
    def packages_known(self) -> bool:
        """False when the package list couldn't be read, so nothing is known to be missing."""
        self.packages
        return self._packages_known

    def installed(self, *patterns: str) -> list[str]:
        """Installed packages matching any name or fnmatch pattern."""
        return sorted({p for p in self.packages for pat in patterns if fnmatch.fnmatchcase(p, pat)})

    def in_repos(self, package: str) -> bool:
        return self.family == "arch" and self.run("pacman", "-Si", package).ok

    @property
    def kernels(self) -> list[str]:
        """Package names of the installed kernels (Arch writes these to pkgbase)."""
        names = {(self.read(f) or "").strip() for f in self.glob("/usr/lib/modules/*/pkgbase")}
        return sorted(n for n in names if n)

    def kernels_without(self, module: str) -> list[str]:
        """Installed kernels that have no copy of a module, whether built in, packaged or from DKMS."""
        missing = set()
        for pkgbase in self.glob("/usr/lib/modules/*/pkgbase"):
            name = (self.read(pkgbase) or "").strip()
            if name and not glob.glob(f"{glob.escape(str(pkgbase.parent))}/**/{module}.ko*", recursive=True):
                missing.add(name)
        return sorted(missing)

    # Hardware

    @property
    def pci(self) -> list[PciDevice]:
        if self._pci is None:
            names: dict[str, str] = {}
            for line in self.run("lspci", "-nn", "-D").out.splitlines():
                slot, _, rest = line.partition(" ")
                # "VGA compatible controller [0300]: NVIDIA Corporation GB203 [...] [10de:2c02] (rev a1)"
                match = re.search(r"\[[0-9a-f]{4}\]:\s*(.*?)\s*\[[0-9a-f]{4}:[0-9a-f]{4}\]", rest)
                if match:
                    names[slot] = match.group(1)
            self._pci = []
            for dev in self.glob("/sys/bus/pci/devices/*"):
                read = lambda n: (self.read(dev / n) or "").strip()
                driver = dev / "driver"
                self._pci.append(PciDevice(
                    slot=dev.name,
                    vendor=read("vendor").removeprefix("0x").lower(),
                    device=read("device").removeprefix("0x").lower(),
                    cls=read("class").removeprefix("0x").lower(),
                    driver=os.path.basename(os.readlink(driver)) if driver.is_symlink() else None,
                    name=names.get(dev.name, ""),
                ))
        return self._pci

    @property
    def modules(self) -> set[str]:
        text = self.read("/proc/modules") or ""
        return {line.split()[0] for line in text.splitlines() if line.strip()}

    @property
    def cmdline(self) -> list[str]:
        return (self.read("/proc/cmdline") or "").split()

    @property
    def boot_time(self) -> float:
        for line in (self.read("/proc/stat") or "").splitlines():
            if line.startswith("btime "):
                return float(line.split()[1])
        return 0.0

    def journal_cursor(self) -> str | None:
        """Position in the journal right now, used to read only what happens next."""
        out = self._exec(["journalctl", "-n", "0", "--show-cursor", "--no-pager"], 15).out
        match = re.search(r"cursor:\s*(\S+)", out)
        return match.group(1) if match else None

    def clear_cache(self) -> None:
        self._cache.clear()
