"""Small helpers for reading YAML into checked values.

Every value is read through a Ctx, which knows the file and the field path, so
each error names both: "nodes/neo/node.yml: apps[2].images[0].build.context".
"""

import re
from pathlib import Path

from .errors import ConfigError

MISSING = object()


class Ctx:
    def __init__(self, file: str, path: str = ""):
        self.file = file
        self.path = path

    def child(self, key: str) -> "Ctx":
        return Ctx(self.file, f"{self.path}.{key}" if self.path else key)

    def item(self, index: int) -> "Ctx":
        return Ctx(self.file, f"{self.path}[{index}]")

    @property
    def where(self) -> str:
        return f"{self.file}: {self.path}" if self.path else self.file

    def fail(self, message: str) -> ConfigError:
        return ConfigError(self.where, message)


def as_map(value, ctx: Ctx, allowed=None, required=()) -> dict:
    if value is None:
        value = {}
    if not isinstance(value, dict):
        raise ctx.fail(f"expected a mapping, got {type(value).__name__}")
    if allowed is not None:
        unknown = sorted(set(value) - set(allowed))
        if unknown:
            raise ctx.fail(f"unknown key(s) {', '.join(map(str, unknown))} (allowed: {', '.join(sorted(allowed))})")
    for key in required:
        if key not in value or value[key] is None:
            raise ctx.child(key).fail("is required")
    return value


def as_list(value, ctx: Ctx) -> list:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ctx.fail(f"expected a list, got {type(value).__name__}")
    return value


def get_str(d: dict, key: str, ctx: Ctx, default=MISSING, choices=None, pattern=None) -> str:
    c = ctx.child(key)
    if key not in d or d[key] is None:
        if default is MISSING:
            raise c.fail("is required")
        return default
    value = d[key]
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        raise c.fail(f"expected a string, got {type(value).__name__}")
    value = str(value)
    if choices is not None and value not in choices:
        raise c.fail(f"{value!r} is not one of {', '.join(choices)}")
    if pattern is not None and not re.fullmatch(pattern, value):
        raise c.fail(f"{value!r} is not a valid value")
    return value


def get_bool(d: dict, key: str, ctx: Ctx, default=MISSING):
    c = ctx.child(key)
    if key not in d or d[key] is None:
        if default is MISSING:
            raise c.fail("is required")
        return default
    if not isinstance(d[key], bool):
        raise c.fail(f"expected true or false, got {d[key]!r}")
    return d[key]


def get_int(d: dict, key: str, ctx: Ctx, default=MISSING, minimum=None) -> int:
    c = ctx.child(key)
    if key not in d or d[key] is None:
        if default is MISSING:
            raise c.fail("is required")
        return default
    value = d[key]
    if isinstance(value, bool) or not isinstance(value, int):
        raise c.fail(f"expected a whole number, got {value!r}")
    if minimum is not None and value < minimum:
        raise c.fail(f"must be at least {minimum}")
    return value


def get_str_list(d: dict, key: str, ctx: Ctx) -> list:
    c = ctx.child(key)
    out = []
    for i, v in enumerate(as_list(d.get(key), c)):
        if isinstance(v, bool) or not isinstance(v, (str, int, float)):
            raise c.item(i).fail(f"expected a string, got {type(v).__name__}")
        out.append(str(v))
    return out


def get_mode(d: dict, key: str, ctx: Ctx) -> int:
    """A file mode, written as an octal STRING ("0755").

    An unquoted 0755 is already octal to YAML 1.1, but 755 is seven hundred and
    fifty-five; requiring a string makes the two impossible to confuse.
    """
    c = ctx.child(key)
    if key not in d or d[key] is None:
        raise c.fail("is required")
    value = d[key]
    if not isinstance(value, str) or not re.fullmatch(r"0?[0-7]{3}", value):
        raise c.fail(f"{value!r} is not an octal mode; write it as a quoted string, e.g. \"0755\"")
    return int(value, 8)


def node_path(value: str, ctx: Ctx) -> str:
    """An absolute path on the node, normalised. '/' itself and '..' are refused."""
    if not value.startswith("/"):
        raise ctx.fail(f"{value!r} must be an absolute path on the node")
    parts = [p for p in value.split("/") if p not in ("", ".")]
    if not parts or ".." in parts:
        raise ctx.fail(f"{value!r} is not a usable path on the node")
    return "/" + "/".join(parts)


def local_path(value: str, base: Path) -> Path:
    """A path on the build machine: relative ones are relative to the file they are written in."""
    p = Path(value).expanduser()
    return p if p.is_absolute() else (base / p).resolve()


def local_dir(d: dict, key: str, ctx: Ctx, base: Path):
    if key not in d or d[key] in (None, ""):
        return None
    p = local_path(get_str(d, key, ctx), base)
    if not p.is_dir():
        raise ctx.child(key).fail(f"points at {p}, which is not a directory")
    return p


def local_file(d: dict, key: str, ctx: Ctx, base: Path, required=False):
    if key not in d or d[key] in (None, ""):
        if required:
            raise ctx.child(key).fail("is required")
        return None
    p = local_path(get_str(d, key, ctx), base)
    if not p.is_file():
        raise ctx.child(key).fail(f"points at {p}, which is not a file")
    return p
