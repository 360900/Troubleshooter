import os
import sys
import traceback


def _terminal_instead(reason: str) -> int:
    """Starts the terminal version when the window can't open, if there's someone to use it."""
    if not sys.stdin.isatty():
        print(reason, file=sys.stderr)
        return 1
    print(f"{reason} Starting the terminal version instead.", file=sys.stderr)
    from .interactive import main as interactive
    return interactive([])


def main() -> int:
    if len(sys.argv) > 1:
        from .cli import main as cli
        return cli(sys.argv[1:])
    if not (os.environ.get("WAYLAND_DISPLAY") or os.environ.get("DISPLAY")):
        return _terminal_instead("No desktop to open the window on.")
    try:
        from .ui.app import run
    except ImportError as err:
        return _terminal_instead(f"Couldn't load the window ({err}).")
    try:
        return run()
    except Exception:
        traceback.print_exc()
        return _terminal_instead("The window failed to start.")


if __name__ == "__main__":
    sys.exit(main())
