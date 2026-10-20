"""The 'builder:' range in deployment.yml, checked against this tool's VERSION.

A range is a comma-separated list of comparisons, e.g. ">=1.0,<2". Versions
compare numerically per component, and a missing component counts as 0, so
"<2" means "below 2.0.0".
"""

import operator
import re

from .errors import ConfigError

_OPS = {
    ">=": operator.ge,
    "<=": operator.le,
    "==": operator.eq,
    "!=": operator.ne,
    ">": operator.gt,
    "<": operator.lt,
}
_CLAUSE = re.compile(r"^\s*(>=|<=|==|!=|>|<)\s*(\d+(?:\.\d+)*)\s*$")


def parse_version(text: str) -> tuple:
    if not re.fullmatch(r"\d+(\.\d+)*", text.strip()):
        raise ValueError(f"not a version: {text!r}")
    parts = tuple(int(p) for p in text.strip().split("."))
    return parts + (0,) * (3 - len(parts))


def satisfies(version: str, spec: str, where: str = "deployment.yml: builder") -> bool:
    have = parse_version(version)
    clauses = [c for c in spec.split(",") if c.strip()]
    if not clauses:
        raise ConfigError(where, f"empty version range {spec!r}")
    for clause in clauses:
        m = _CLAUSE.match(clause)
        if not m:
            raise ConfigError(where, f"cannot read {clause.strip()!r} in {spec!r} (write e.g. \">=1.0,<2\")")
        op, ver = m.groups()
        if not _OPS[op](have, parse_version(ver)):
            return False
    return True
