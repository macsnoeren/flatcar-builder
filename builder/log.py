import os
import sys

_COLOUR = sys.stderr.isatty()


def _paint(code: str, text: str) -> str:
    return f"\033[{code}m{text}\033[0m" if _COLOUR else text


def info(msg: str) -> None:
    if os.environ.get("FLATCAR_BUILDER_QUIET") == "1":
        return
    print(_paint("1;34", f"[+] {msg}"), file=sys.stderr, flush=True)


def warn(msg: str) -> None:
    if os.environ.get("FLATCAR_BUILDER_QUIET") == "1":
        return
    print(_paint("1;33", f"[!] {msg}"), file=sys.stderr, flush=True)


def error(msg: str) -> None:
    print(_paint("1;31", f"[x] {msg}"), file=sys.stderr, flush=True)
