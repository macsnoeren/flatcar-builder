"""Flatcar Builder: turns a deployment directory into Flatcar installs and update bundles."""

from pathlib import Path

TOOL_DIR = Path(__file__).resolve().parent.parent
NODE_DIR = TOOL_DIR / "node"


def tool_version() -> str:
    return (TOOL_DIR / "VERSION").read_text().strip()
