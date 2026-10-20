import subprocess
from pathlib import Path


def describe(path: Path) -> dict:
    """{"commit": sha or None, "dirty": bool or None} for the checkout path is in."""
    def git(*args):
        r = subprocess.run(["git", "-C", str(path), *args], capture_output=True, text=True)
        return r.stdout.strip() if r.returncode == 0 else None

    try:
        commit = git("rev-parse", "HEAD")
    except FileNotFoundError:
        return {"commit": None, "dirty": None}
    if commit is None:
        return {"commit": None, "dirty": None}
    status = git("status", "--porcelain", "--", ".")
    return {"commit": commit, "dirty": bool(status)}
