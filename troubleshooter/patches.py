"""Compiles the steps of a database fix into a plan: plain sentences for the reader, and
the exact root script that does it.

The script is what runs, and advanced users can edit it. It backs up every file before
touching it. Undo restores those backups and reverses package and service changes.
"""
from __future__ import annotations

import re
import shlex
import time
from dataclasses import dataclass, field

from . import boot
from .system import System

BACKUP_ROOT = "/var/lib/troubleshooter/backups"
_PARAM = re.compile(r"^[A-Za-z0-9_.\-]+(=[A-Za-z0-9_.,:/\-]+)?$")
_PACKAGE = re.compile(r"^[A-Za-z0-9@._+*\-]+$")
_MODULE = re.compile(r"^[A-Za-z0-9_\-]+$")

_PRELUDE = r'''
ts_backup() {
  local f="$1"
  if [ -e "$f" ]; then
    [ -e "$TS_BACKUP/files$f" ] && return 0
    mkdir -p "$TS_BACKUP/files$(dirname "$f")"
    cp -a "$f" "$TS_BACKUP/files$f"
  else
    grep -qxF "$f" "$TS_BACKUP/absent" 2>/dev/null || printf '%s\n' "$f" >> "$TS_BACKUP/absent"
  fi
}
# Append params to each line of a file matching a regex, skipping ones already there.
ts_append_cmdline() {
  local f="$1" re="$2" p
  for p in $3; do
    grep -Eq "(^|[[:space:]=\"'])$p([[:space:]\"']|$)" "$f" || sed -E -i "/$re/ s|[[:space:]]*\$| $p|" "$f"
  done
}
# Append params inside the quotes of NAME="..." , adding the line if it's missing.
ts_append_quoted() {
  local f="$1" name="$2" p
  grep -q "^$name=" "$f" || printf '%s=""\n' "$name" >> "$f"
  for p in $3; do
    grep -Eq "^$name=.*[\"' ]$p([\"' ]|$)" "$f" ||
      sed -E -i "s|^($name=)([\"']?)(.*)\2[[:space:]]*\$|\1\2\3 $p\2|; s|^($name=[\"']?) |\1|" "$f"
  done
}
ts_append_grub() { ts_append_quoted "$1" GRUB_CMDLINE_LINUX_DEFAULT "$2"; }
'''.strip()

_UNDO_PRELUDE = r'''
ts_restore_all() {
  if [ -d "$TS_BACKUP/files" ]; then
    (cd "$TS_BACKUP/files" && find . -type f -print0 | while IFS= read -r -d '' f; do
      cp -a "$f" "/${f#./}"
    done)
  fi
  if [ -f "$TS_BACKUP/absent" ]; then
    while IFS= read -r f; do rm -f -- "$f"; done < "$TS_BACKUP/absent"
  fi
}
'''.strip()


@dataclass
class Step:
    says: str
    lines: list[str] = field(default_factory=list)
    undo: list[str] = field(default_factory=list)
    undo_first: list[str] = field(default_factory=list)  # undone before files are restored
    late: list[str] = field(default_factory=list)  # runs once, after everything else
    late_undo: list[str] = field(default_factory=list)


@dataclass
class Plan:
    entry_id: str
    title: str
    run_id: str
    steps: list[Step]
    reboot: bool
    script: str
    undo_script: str

    @property
    def summary(self) -> list[str]:
        return [s.says for s in self.steps]


class PlanError(Exception):
    """This fix can't be turned into a plan on this machine."""


def _q(value: str) -> str:
    return shlex.quote(value)


# Package managers

def _pm_install(system: System, packages: list[str]) -> str:
    names = " ".join(_q(p) for p in packages)
    match system.family:
        case "arch":
            return f"pacman -S --needed --noconfirm {names}"
        case "debian":
            return f"DEBIAN_FRONTEND=noninteractive apt-get install -y {names}"
        case "fedora":
            return f"dnf install -y {names}"
    raise PlanError("No known package manager on this system.")


def _pm_remove(system: System, packages: list[str], force: bool = False) -> str:
    names = " ".join(_q(p) for p in packages)
    match system.family:
        case "arch":
            return f"pacman -Rdd --noconfirm {names}" if force else f"pacman -Rns --noconfirm {names}"
        case "debian":
            return f"apt-get remove -y {names}"
        case "fedora":
            return f"dnf remove -y {names}"
    raise PlanError("No known package manager on this system.")


def _listing(items: list[str]) -> str:
    return ", ".join(items[:-1]) + f" and {items[-1]}" if len(items) > 1 else items[0]


def _headers(system: System) -> list[str]:
    match system.family:
        case "arch":
            return [f"{k}-headers" for k in system.kernels]
        case "debian":
            return ["linux-headers-amd64"]
        case "fedora":
            return ["kernel-devel"]
    return []


def _expand_packages(step: dict, system: System) -> list[str]:
    """Named packages, plus {optional = needs} ones whose companion is installed."""
    packages = []
    for name in step.get("packages", []):
        packages += _headers(system) if name == "{headers}" else [name]
    for name, needs in step.get("optional", {}).items():
        if system.installed(*([needs] if isinstance(needs, str) else needs)):
            packages.append(name)
    return packages


# Ops. Each takes the step's table and the machine, and returns a Step (or None if
# there's nothing to do here).

def _op_install(step: dict, system: System, entry_id: str) -> Step | None:
    packages = _expand_packages(step, system)
    missing = [p for p in packages if p not in system.packages]
    if not missing:
        return None
    return Step(step.get("says") or f"Install {_listing(missing)}",
                [_pm_install(system, missing)], [_pm_remove(system, missing)])


def _op_remove(step: dict, system: System, entry_id: str) -> Step | None:
    present = system.installed(*step["packages"])
    if not present:
        return None
    return Step(step.get("says") or f"Remove {_listing(present)}",
                [_pm_remove(system, present, step.get("force", False))], [_pm_install(system, present)])


def _aur_build(pkgbase: str, wanted: list[str]) -> list[str]:
    """Build as the invoking user (makepkg refuses root), then hand the packages to pacman."""
    url = f"https://aur.archlinux.org/{pkgbase}.git"
    src = f'"$TS_BUILD/{pkgbase}"'
    names = " ".join(_q(w) for w in wanted)
    return [
        f'runuser -u "$TS_USER" -- git clone --depth 1 {_q(url)} {src}',
        f'ts_make="$(cd {src} && runuser -u "$TS_USER" -- makepkg --printsrcinfo | sed -nE \'s/^[[:space:]]*makedepends = ([^<>=]*).*/\\1/p\' | sort -u)"',
        '[ -z "$ts_make" ] || pacman -S --needed --noconfirm --asdeps $ts_make',
        f'(cd {src} && runuser -u "$TS_USER" -- makepkg -f --nodeps --noconfirm)',
        f'for p in {names}; do ls {src}/"$p"-[0-9]*.pkg.tar.* >> "$TS_BUILD/built"; done',
    ]


def _op_install_aur(step: dict, system: System, entry_id: str) -> Step | None:
    """Replace packages with ones that may only exist in the AUR.

    The order matters: build first, because a failed build must leave the working
    driver alone. Only then remove the old packages and install the new ones.
    """
    packages = _expand_packages(step, system)
    bases: dict[str, str] = step.get("aur_bases", {})
    from_repo = [p for p in packages if p not in bases or system.in_repos(p)]
    from_aur = [p for p in packages if p not in from_repo]
    old = system.installed(*step.get("replaces", []))

    lines, says = [], []
    if from_aur:
        says.append(f"Build {_listing(from_aur)} from the AUR (community-maintained)")
        lines += ["pacman -S --needed --noconfirm base-devel git",
                  'TS_USER="$(id -nu "${PKEXEC_UID:?}")"',
                  'TS_BUILD="$(runuser -u "$TS_USER" -- mktemp -d)"', "trap 'rm -rf \"$TS_BUILD\"' EXIT"]
        by_base: dict[str, list[str]] = {}
        for p in from_aur:
            by_base.setdefault(bases[p], []).append(p)
        for base, wanted in by_base.items():
            lines += _aur_build(base, wanted)
    if old:
        says.append(f"Remove {_listing(old)}")
        lines.append(_pm_remove(system, old, force=True))
    if from_repo:
        says.append(f"Install {_listing(from_repo)}")
        lines.append(_pm_install(system, from_repo))
    if from_aur:
        lines.append('xargs -r pacman -U --noconfirm < "$TS_BUILD/built"')

    undo = [_pm_remove(system, packages, force=True)]
    if old:
        undo.append(_pm_install(system, old))
    return Step(step.get("says") or ". ".join(says), lines, undo)


def _op_kernel_params(step: dict, system: System, entry_id: str) -> Step | None:
    params = [p for p in step["add"] if not boot.param_locations(system, p)]
    if not params:
        return None
    targets = boot.targets(system, params)
    lines, undo, late = [], [], []
    for target in targets:
        lines += target.lines
        undo += target.undo
        late += target.regen
    where = _listing([f"{t.name} ({t.path})" for t in targets])
    return Step(step.get("says") or f"Add {_listing(params)} to the kernel parameters in {where}",
                lines, undo, late, late)


def _initramfs_command(system: System) -> str:
    match system.family:
        case "arch":
            # mkinitcpio -P alone leaves Limine's boot entries out of date.
            if "limine" in boot.loaders(system) and (
                    system.exists("/usr/bin/limine-update") or system.installed("limine-mkinitcpio-hook")):
                return "limine-update"
            return "mkinitcpio -P"
        case "debian":
            return "update-initramfs -u -k all"
        case "fedora":
            return "dracut --regenerate-all --force"
    raise PlanError("No known way to rebuild the initramfs on this system.")


def _op_initramfs(step: dict, system: System, entry_id: str) -> Step:
    command = _initramfs_command(system)
    return Step(step.get("says") or f"Rebuild the boot images ({command})",
                late=[command], late_undo=[command])


def _write(system: System, path: str, content: str, mode: str = "644") -> list[str]:
    real = _q(str(system.path(path)))
    if not content.endswith("\n"):
        content += "\n"
    return [
        f"ts_backup {real}",
        f"mkdir -p \"$(dirname {real})\"",
        f"cat > {real} <<'TS_EOF'\n{content}TS_EOF",
        f"chmod {mode} {real}",
    ]


def _op_write_file(step: dict, system: System, entry_id: str) -> Step:
    return Step(step.get("says") or f"Write {step['path']}",
                _write(system, step["path"], step["content"], step.get("mode", "644")))


def _op_replace(step: dict, system: System, entry_id: str) -> Step:
    path = _q(str(system.path(step["path"])))
    script = f"{step.get('address', '')}s|{step['pattern']}|{step['replacement']}|"
    return Step(step["says"], [f"ts_backup {path}", f"sed -E -i {_q(script)} {path}"])


def _op_module_options(step: dict, system: System, entry_id: str) -> Step:
    module, options = step["module"], step["options"]
    content = f"# Added by Troubleshooter: {entry_id}\noptions {module} {' '.join(options)}\n"
    return Step(step.get("says") or f"Set {_listing(options)} for the {module} module",
                _write(system, f"/etc/modprobe.d/troubleshooter-{entry_id}-{module}.conf", content))


def _op_blacklist(step: dict, system: System, entry_id: str) -> Step:
    modules = step["modules"]
    body = "".join(f"blacklist {m}\n" for m in modules)
    return Step(step.get("says") or f"Stop {_listing(modules)} from loading",
                _write(system, f"/etc/modprobe.d/troubleshooter-{entry_id}-blacklist.conf",
                       f"# Added by Troubleshooter: {entry_id}\n{body}"))


def _op_sysctl(step: dict, system: System, entry_id: str) -> Step:
    pairs = step["values"]
    body = "".join(f"{k} = {v}\n" for k, v in pairs.items())
    says = ", ".join(f"{k} to {v}" for k, v in pairs.items())
    return Step(step.get("says") or f"Set {says}",
                _write(system, f"/etc/sysctl.d/90-troubleshooter-{entry_id}.conf", body),
                late=["sysctl --system > /dev/null"], late_undo=["sysctl --system > /dev/null"])


def _op_service(step: dict, system: System, entry_id: str) -> Step | None:
    action = step.get("action", "enable")
    undo_action = {"enable": "disable", "disable": "enable", "mask": "unmask"}[action]
    change = []
    for name in step["names"]:
        state = system.run("systemctl", "is-enabled", name).out.strip()
        done = {"enable": state in ("enabled", "static", "alias"), "disable": state == "disabled",
                "mask": state == "masked"}[action]
        if not done:
            change.append(name)
    if not change:
        return None
    names = " ".join(_q(n) for n in change)
    now = " --now" if step.get("now") else ""
    verb = {"enable": "Enable", "disable": "Disable", "mask": "Mask"}[action]
    # Undone before files are restored: a unit file the fix wrote must still exist to be disabled.
    return Step(step.get("says") or f"{verb} {_listing(change)}",
                [f"systemctl {action}{now} {names}"], undo_first=[f"systemctl {undo_action}{now} {names}"])


def _op_command(step: dict, system: System, entry_id: str) -> Step:
    return Step(step["says"], [step["run"]], [step["undo"]] if step.get("undo") else [])


OPS = {
    "install": _op_install,
    "remove": _op_remove,
    "install_aur": _op_install_aur,
    "kernel_params": _op_kernel_params,
    "initramfs": _op_initramfs,
    "write_file": _op_write_file,
    "replace": _op_replace,
    "module_options": _op_module_options,
    "blacklist": _op_blacklist,
    "sysctl": _op_sysctl,
    "service": _op_service,
    "command": _op_command,
}
_REQUIRED = {
    "install": ("packages",), "remove": ("packages",), "install_aur": ("packages",),
    "kernel_params": ("add",), "write_file": ("path", "content"),
    "replace": ("path", "pattern", "replacement", "says"), "module_options": ("module", "options"),
    "blacklist": ("modules",), "sysctl": ("values",), "service": ("names",), "command": ("says", "run"),
}


def validate_step(step: dict) -> str | None:
    """Why this step is malformed, or None. Values end up in a root shell script."""
    op = step["op"]
    for key in _REQUIRED.get(op, ()):
        if key not in step:
            return f"{op} needs {key}"
    for param in step.get("add", []):
        if not _PARAM.match(param):
            return f"bad kernel parameter {param!r}"
    for name in [*step.get("packages", []), *step.get("replaces", []), *step.get("optional", {})]:
        if name != "{headers}" and not _PACKAGE.match(name):
            return f"bad package name {name!r}"
    for module in [*step.get("modules", []), *([step["module"]] if "module" in step else [])]:
        if not _MODULE.match(module):
            return f"bad module name {module!r}"
    for option in step.get("options", []):
        if not _PARAM.match(option):
            return f"bad module option {option!r}"
    if "|" in step.get("pattern", "") or "|" in step.get("replacement", ""):
        return "replace can't use | because it's the sed delimiter"
    if step.get("action", "enable") not in ("enable", "disable", "mask"):
        return f"bad service action {step['action']!r}"
    return None


def new_run_id(entry_id: str) -> str:
    return f"{time.strftime('%Y%m%d-%H%M%S')}-{entry_id}"


def _ordered_unique(items: list[str]) -> list[str]:
    return list(dict.fromkeys(items))


def build_plan(entry_id: str, title: str, steps: list[dict], reboot: bool, system: System,
               run_id: str | None = None) -> Plan:
    run_id = run_id or new_run_id(entry_id)
    compiled = []
    for raw in steps:
        step = OPS[raw["op"]](raw, system, entry_id)
        if step:
            compiled.append(step)
    if not compiled:
        raise PlanError("Nothing to do: everything this fix changes is already in place.")

    backup = str(system.path(f"{BACKUP_ROOT}/{run_id}"))
    head = [
        "#!/bin/bash",
        f"# Troubleshooter: {title}",
        f"# Run {run_id}. Files are backed up to {backup}",
        f"export PATH={system.script_path}",
        f"TS_BACKUP={_q(backup)}",
    ]
    body = [*head, "set -euo pipefail", 'mkdir -p "$TS_BACKUP"', 'chmod 700 "$TS_BACKUP"', _PRELUDE, ""]
    for step in compiled:
        if step.lines:
            body += [f"echo {_q('==> ' + step.says)}", *step.lines, ""]
    late = _ordered_unique([line for step in compiled for line in step.late])
    says = [step.says for step in compiled if step.late and not step.lines]
    if late:
        body += [f"echo {_q('==> ' + (says[0] if says else 'Updating boot files'))}", *late, ""]
    body.append("echo '==> Done'")

    undo = [*head[:-2], f"# Undoes run {run_id}", f"export PATH={system.script_path}", f"TS_BACKUP={_q(backup)}",
            '[ -d "$TS_BACKUP" ] || { echo "Backup $TS_BACKUP is gone, nothing to restore from." >&2; exit 1; }',
            "rc=0", "set -E", "trap 'rc=1' ERR", _UNDO_PRELUDE, ""]
    for step in reversed(compiled):
        if step.undo_first:
            undo += [f"echo {_q('==> Reversing: ' + step.says)}", *step.undo_first]
    undo += ["echo '==> Restoring files'", "ts_restore_all"]
    for step in reversed(compiled):
        if step.undo:
            undo += [f"echo {_q('==> Reversing: ' + step.says)}", *step.undo]
    late_undo = _ordered_unique([line for step in compiled for line in step.late_undo])
    if late_undo:
        undo += ["echo '==> Updating boot files'", *late_undo]
    undo += ["echo '==> Done'", 'exit "$rc"']

    return Plan(entry_id, title, run_id, compiled, reboot, "\n".join(body) + "\n", "\n".join(undo) + "\n")
