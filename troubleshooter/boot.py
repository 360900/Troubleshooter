"""Kernel command line: find which boot setup is in use and how to change it.

Several configs can coexist (this happens on CachyOS: grub's and Limine's are both
present), so the loader the firmware actually started wins over what merely exists.
/boot is usually root-only, so anything under it is handled at run time by the
root script, not read here.
"""
from __future__ import annotations

import shlex
from dataclasses import dataclass, field

from .system import System

EFI_GLOBAL = "8be4df61-93ca-11d2-aa0d-00e098032b8c"
LOADERS = ("limine", "grub", "systemd-boot")


def _efi_text(data: bytes) -> str:
    # The description is UTF-16, the path after it may start on an odd offset.
    return (data.decode("utf-16-le", "ignore") + data[1:].decode("utf-16-le", "ignore")).lower()


def _match_loader(text: str) -> str | None:
    if "limine" in text:
        return "limine"
    if "grub" in text:
        return "grub"
    if "systemd" in text:
        return "systemd-boot"
    return None


def active_loader(system: System) -> str | None:
    """The loader that booted this system, or None when the firmware can't tell us."""
    current = system.read_bytes(f"/sys/firmware/efi/efivars/BootCurrent-{EFI_GLOBAL}")
    if current and len(current) >= 6:
        number = int.from_bytes(current[4:6], "little")
        option = system.read_bytes(f"/sys/firmware/efi/efivars/Boot{number:04X}-{EFI_GLOBAL}")
        loader = option and _match_loader(_efi_text(option))
        if loader:
            return loader
    # A generic \EFI\BOOT\BOOTX64.EFI says nothing; the loader may have told us itself.
    for var in system.glob("/sys/firmware/efi/efivars/LoaderInfo-*"):
        try:
            loader = _match_loader(_efi_text(var.read_bytes()))
        except OSError:
            continue
        if loader:
            return loader
    return None


@dataclass
class Target:
    """One place the kernel command line is configured."""
    name: str
    path: str  # shown to the user
    lines: list[str] = field(default_factory=list)
    undo: list[str] = field(default_factory=list)
    regen: list[str] = field(default_factory=list)


def _q(*params: str) -> str:
    return shlex.quote(" ".join(params))


def _limine(system: System, params: list[str]) -> Target:
    if system.exists("/usr/bin/limine-update") or system.installed("limine-mkinitcpio-hook", "limine-dracut-support"):
        path = str(system.path("/etc/default/limine"))
        line = f'KERNEL_CMDLINE[default]+="{" ".join(params)}"'
        return Target("Limine", "/etc/default/limine", [
            f"ts_backup {shlex.quote(path)}",
            f"printf '%s\\n' {shlex.quote(line)} >> {shlex.quote(path)}",
        ], regen=["limine-update"])
    candidates = " ".join(shlex.quote(str(system.path(p))) for p in (
        "/boot/limine.conf", "/boot/limine/limine.conf", "/boot/EFI/limine/limine.conf",
        "/boot/EFI/BOOT/limine.conf", "/efi/limine.conf"))
    return Target("Limine", "limine.conf", [
        f"for f in {candidates}; do",
        '  [ -f "$f" ] || continue',
        '  ts_backup "$f"',
        f"  ts_append_cmdline \"$f\" '^[[:space:]]*(kernel_)?cmdline:' {_q(*params)}",
        "done",
    ])


def _grub(system: System, params: list[str]) -> Target:
    path = shlex.quote(str(system.path("/etc/default/grub")))
    regen = {"debian": "update-grub"}.get(system.family, "grub-mkconfig -o /boot/grub/grub.cfg")
    return Target("GRUB", "/etc/default/grub", [
        f"ts_backup {path}",
        f"ts_append_grub {path} {_q(*params)}",
    ], regen=[regen])


def _grubby(system: System, params: list[str]) -> Target:
    return Target("GRUB", "grubby (all kernels)",
                  [f"grubby --update-kernel=ALL --args={_q(*params)}"],
                  undo=[f"grubby --update-kernel=ALL --remove-args={_q(*params)}"])


def _systemd_boot(system: System, params: list[str]) -> Target:
    if system.exists("/etc/sdboot-manage.conf"):
        path = shlex.quote(str(system.path("/etc/sdboot-manage.conf")))
        return Target("systemd-boot", "/etc/sdboot-manage.conf", [
            f"ts_backup {path}",
            f"ts_append_quoted {path} LINUX_OPTIONS {_q(*params)}",
        ], regen=["sdboot-manage gen"])
    dirs = " ".join(shlex.quote(str(system.path(d))) + "/*.conf" for d in (
        "/boot/loader/entries", "/efi/loader/entries", "/boot/efi/loader/entries"))
    return Target("systemd-boot", "loader entries", [
        f"for f in {dirs}; do",
        '  [ -f "$f" ] || continue',
        '  ts_backup "$f"',
        f"  ts_append_cmdline \"$f\" '^options[[:space:]]' {_q(*params)}",
        "done",
    ])


def _uki(system: System, params: list[str]) -> Target:
    """Unified kernel images built by mkinitcpio embed the command line."""
    if system.exists("/etc/kernel/cmdline"):
        path = shlex.quote(str(system.path("/etc/kernel/cmdline")))
        return Target("Unified kernel image", "/etc/kernel/cmdline", [
            f"ts_backup {path}",
            f"ts_append_cmdline {path} '.' {_q(*params)}",
        ], regen=["mkinitcpio -P"])
    path = shlex.quote(str(system.path("/etc/cmdline.d/troubleshooter.conf")))
    return Target("Unified kernel image", "/etc/cmdline.d/troubleshooter.conf", [
        f"ts_backup {path}",
        f"mkdir -p {shlex.quote(str(system.path('/etc/cmdline.d')))}",
        f"printf '%s\\n' {_q(*params)} >> {path}",
    ], regen=["mkinitcpio -P"])


def uses_uki(system: System) -> bool:
    for preset in system.glob("/etc/mkinitcpio.d/*.preset"):
        for line in (system.read(preset) or "").splitlines():
            if "_uki=" in line and not line.lstrip().startswith("#"):
                return True
    return False


def loaders(system: System) -> list[str]:
    """Loaders to update: the active one, or every one that looks installed."""
    active = active_loader(system)
    if active:
        return [active]
    found = []
    if system.exists("/etc/default/limine") or system.installed("limine"):
        found.append("limine")
    if system.exists("/etc/default/grub") and (system.installed("grub", "grub2*") or system.family == "fedora"):
        found.append("grub")
    return found or ["systemd-boot"]


def targets(system: System, params: list[str]) -> list[Target]:
    result = []
    for loader in loaders(system):
        if loader == "limine":
            result.append(_limine(system, params))
        elif loader == "grub":
            result.append(_grubby(system, params) if system.family == "fedora" else _grub(system, params))
        else:
            result.append(_systemd_boot(system, params))
    if uses_uki(system):
        result.append(_uki(system, params))
    return result


READABLE_CONFIGS = (
    "/etc/default/limine", "/etc/limine-entry-tool.conf", "/etc/limine-entry-tool.d/*.conf",
    "/etc/default/grub", "/etc/kernel/cmdline", "/etc/cmdline.d/*.conf", "/etc/sdboot-manage.conf",
    "/boot/limine.conf", "/boot/grub/grub.cfg", "/boot/loader/entries/*.conf",
)


def param_locations(system: System, param: str) -> list[str]:
    """Everywhere a kernel parameter (name or name=value) is already set."""
    def normal(token: str) -> str:
        name, sep, value = token.partition("=")
        return name.replace("-", "_") + sep + value

    wanted = normal(param)
    module, _, option = wanted.partition(".")
    found = []
    if wanted in map(normal, system.cmdline):
        found.append("the running kernel command line")
    for pattern in READABLE_CONFIGS:
        for path in system.glob(pattern):
            text = system.read(path)
            if text is None:
                continue
            for line in text.splitlines():
                if line.lstrip().startswith("#"):
                    continue
                tokens = line.replace('"', " ").replace("'", " ").split()
                if wanted in map(normal, tokens):
                    found.append(str(path))
                    break
    if option:
        for pattern in ("/etc/modprobe.d/*.conf", "/usr/lib/modprobe.d/*.conf"):
            for path in system.glob(pattern):
                for line in (system.read(path) or "").splitlines():
                    words = line.split()
                    if len(words) > 2 and words[0] == "options" and words[1].replace("-", "_") == module \
                            and option in map(normal, words[2:]):
                        found.append(str(path))
    return found
