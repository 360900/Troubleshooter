"""Evidence sources. A database entry names a source and how to match it."""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from . import boot
from .system import System

EXCERPTS = 3
EXCERPT_WIDTH = 180


@dataclass
class Evidence:
    matched: bool
    lines: list[str] = field(default_factory=list)
    known: bool = True  # False when the source couldn't be read at all
    in_window: bool = False  # True when it showed up while reproducing


def _hex_ranges(items: list[str]) -> list[tuple[int, int]]:
    out = []
    for item in items:
        low, _, high = item.partition("-")
        out.append((int(low, 16), int(high or low, 16)))
    return out


def _pci(system: System, spec: dict) -> list[str]:
    ranges = _hex_ranges(spec.get("devices", []))
    lines = []
    for dev in system.pci:
        if spec.get("vendor") and dev.vendor != spec["vendor"]:
            continue
        if spec.get("class") and not dev.cls.startswith(spec["class"]):
            continue
        if ranges and not any(lo <= int(dev.device, 16) <= hi for lo, hi in ranges):
            continue
        if "driver" in spec:
            want = spec["driver"]
            if (dev.driver or "none") != want:
                continue
        lines.append(f"{dev} [{dev.vendor}:{dev.device}], driver: {dev.driver or 'none'}")
    return lines


def _usb(system: System, spec: dict) -> list[str]:
    lines = []
    for dev in system.glob("/sys/bus/usb/devices/*"):
        vendor = (system.read(dev / "idVendor") or "").strip()
        if not vendor or (spec.get("vendor") and vendor != spec["vendor"]):
            continue
        product = (system.read(dev / "idProduct") or "").strip()
        if spec.get("devices") and product not in spec["devices"]:
            continue
        lines.append(f"{(system.read(dev / 'product') or '').strip()} [{vendor}:{product}]")
    return lines


def _journal_args(spec: dict) -> list[str]:
    args = ["journalctl", "--no-pager", "-q", "-o", "short", "-n", "50000"]
    if spec.get("kernel"):
        args.append("-k")
    if spec.get("unit"):
        args += ["-u", spec["unit"]]
    if spec.get("identifier"):
        args += ["-t", spec["identifier"]]
    if spec.get("priority"):
        args += ["-p", spec["priority"]]
    return args


def _journal(system: System, spec: dict, window: bool = False) -> list[str] | None:
    if window:
        result = system.run(*_journal_args(spec), f"--after-cursor={system.cursor}")
    else:
        result = system.run(*_journal_args(spec), "-b", str(spec.get("boot", 0)))
    return result.out.splitlines() if result.available else None


def _file(system: System, spec: dict) -> list[str] | None:
    """Lines of the files, or just their names when there is nothing to match against."""
    paths = system.glob(spec["path"])
    if not paths:
        return []
    if "regex" not in spec and "contains" not in spec:
        return [str(p) for p in paths]
    lines, readable = [], False
    for path in paths:
        text = system.read(path)
        if text is not None:
            readable = True
            lines += text.splitlines()[-20000:]
    return lines if readable else None


def _command(system: System, spec: dict) -> list[str] | None:
    result = system.run(*spec["argv"])
    if not result.available:
        return None
    if "exit" in spec and result.code != spec["exit"]:
        return []
    return result.out.splitlines()


def _service(system: System, spec: dict) -> list[str] | None:
    verb = "is-active" if spec.get("state") in ("active", "inactive", "failed") else "is-enabled"
    result = system.run("systemctl", verb, spec["name"])
    if not result.available:
        return None
    state = result.out.strip()
    return [f"{spec['name']}: {state}"] if state == spec.get("state", "enabled") else []


def _kernel_param(system: System, spec: dict) -> list[str]:
    lines = []
    for param in spec["params"]:
        lines += [f"{param} in {place}" for place in boot.param_locations(system, param)]
    return lines


def _kernel_headers(system: System, spec: dict) -> list[str]:
    return [f"{k}-headers is missing" for k in system.kernels if not system.installed(f"{k}-headers")]


SOURCES = {
    "pci": _pci,
    "usb": _usb,
    "journal": _journal,
    "file": _file,
    "command": _command,
    "service": _service,
    "kernel_param": _kernel_param,
    "kernel_headers": _kernel_headers,
    "kernel_modules": lambda system, spec: [f"{k} has no {spec['name']} module" for k in system.kernels_without(spec["name"])],
    "package": lambda system, spec: system.installed(*spec["names"]),
    "module": lambda system, spec: sorted(system.modules & set(spec["names"])),
    "cmdline": lambda system, spec: system.cmdline,
    "env": lambda system, spec: [f"{spec['name']}={system.env[spec['name']]}"] if spec["name"] in system.env else [],
}
GROUPS = {"all_of": all, "any_of": any}
# Every key a spec may use beyond the ones the engine reads.
SOURCE_KEYS = {
    "pci": {"vendor", "class", "devices", "driver"},
    "usb": {"vendor", "devices"},
    "journal": {"kernel", "unit", "identifier", "priority", "boot"},
    "file": {"path"},
    "command": {"argv", "exit"},
    "service": {"name", "state"},
    "kernel_param": {"params"},
    "kernel_headers": set(),
    "kernel_modules": {"name"},
    "package": {"names"},
    "module": {"names"},
    "cmdline": set(),
    "env": {"name"},
}
MATCH_KEYS = {"regex", "contains", "absent"}


def _filter(lines: list[str], spec: dict) -> list[str]:
    if "regex" in spec:
        pattern = re.compile(spec["regex"], re.IGNORECASE)
        return [line for line in lines if pattern.search(line)]
    if "contains" in spec:
        needle = spec["contains"].lower()
        return [line for line in lines if needle in line.lower()]
    return lines


def _excerpts(lines: list[str]) -> list[str]:
    # The most recent lines say the most, and a flood of one message isn't more evidence.
    seen, out = set(), []
    for line in reversed(lines):
        key = re.sub(r"\d+", "#", line)
        if key not in seen:
            seen.add(key)
            out.append(line[:EXCERPT_WIDTH])
        if len(out) == EXCERPTS:
            break
    return out


def probe(system: System, spec: dict) -> Evidence:
    """Check one spec. Sources are never modified, and a failure is simply 'unknown'."""
    for group, combine in GROUPS.items():
        if group in spec:
            parts = [probe(system, part) for part in spec[group]]
            return Evidence(combine(p.matched for p in parts),
                            [line for p in parts if p.matched for line in p.lines],
                            all(p.known for p in parts))
    try:
        lines = SOURCES[spec["source"]](system, spec)
    except (OSError, ValueError, KeyError, re.error):
        lines = None
    if lines is None:
        return Evidence(False, known=False)
    hits = _filter(lines, spec)
    if spec.get("absent"):
        return Evidence(not hits, [])
    return Evidence(bool(hits), _excerpts(hits))


def probe_window(system: System, spec: dict) -> Evidence:
    """Same as probe, but only what the journal recorded since system.cursor."""
    if spec.get("source") != "journal" or not system.cursor or spec.get("absent"):
        return Evidence(False, known=False)
    lines = _journal(system, spec, window=True)
    if lines is None:
        return Evidence(False, known=False)
    hits = _filter(lines, spec)
    return Evidence(bool(hits), _excerpts(hits), in_window=bool(hits))
