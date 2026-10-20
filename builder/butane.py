"""The ignition of a node, through Butane.

The Butane config is built as a data structure and dumped as YAML, so there is
no hand-made indentation to get wrong. Butane itself runs in its container and
turns it into the ignition the installer writes to the disk.
"""

import base64
import os
import shutil
import subprocess
from pathlib import Path

import yaml

from .config import Deployment, Node
from .errors import BuildError
from .rootfs import Tree

BUTANE_IMAGE = "quay.io/coreos/butane:release"
UNIT_DIR = "/etc/systemd/system/"

# Units of the platform that start at boot. docker.service is Flatcar's own.
ENABLED_UNITS = ["docker.service", "import-images.service", "compose-stack.service", "watch-install.service"]

# Flatcar ships two independent update paths and both are switched off: the
# classic update-engine + locksmithd, and systemd-sysupdate with its reboot
# companion (enabled by default from Flatcar 4515). A mask is the only switch
# that holds: these are enabled from /usr, where a disable cannot remove the
# symlink. flatcar-update unmasks update-engine itself for a deliberate update.
MASKED_UNITS = [
    "update-engine.service",
    "locksmithd.service",
    "systemd-sysupdate.service",
    "systemd-sysupdate.timer",
    "systemd-sysupdate-reboot.service",
    "systemd-sysupdate-reboot.timer",
]


class _Dumper(yaml.SafeDumper):
    pass


def _str(dumper, value):
    style = "|" if "\n" in value else None
    return dumper.represent_scalar("tag:yaml.org,2002:str", value, style=style)


_Dumper.add_representer(str, _str)


def is_unit(path: str) -> bool:
    return path.startswith(UNIT_DIR) and "/" not in path[len(UNIT_DIR):]


def _contents(data: bytes) -> dict:
    try:
        text = data.decode("utf-8")
        if "\r" not in text:
            return {"inline": text}
    except UnicodeDecodeError:
        pass
    return {"source": "data:;base64," + base64.b64encode(data).decode()}


def config(dep: Deployment, node: Node, tree: Tree, directories: list) -> dict:
    user = {"name": "core", "ssh_authorized_keys": list(dep.ssh_keys)}
    if dep.core_password_hash:
        user["password_hash"] = dep.core_password_hash

    files = []
    units = {}
    for path in tree.paths():
        entry = tree.entries[path]
        if is_unit(path):
            # Units go in the systemd section, where ignition writes them to the
            # same path; listing them as files as well would be a conflict.
            units[path[len(UNIT_DIR):]] = entry.data().decode()
            continue
        files.append({"path": path, "mode": entry.mode, "overwrite": True, "contents": _contents(entry.data())})

    unit_list = []
    for name in ENABLED_UNITS:
        u = {"name": name, "enabled": True}
        if name in units:
            u["contents"] = units.pop(name)
        unit_list.append(u)
    for name, body in sorted(units.items()):
        unit_list.append({"name": name, "contents": body})
    unit_list += [{"name": name, "mask": True} for name in MASKED_UNITS]

    return {
        "variant": "flatcar",
        "version": "1.0.0",
        "passwd": {"users": [user]},
        "storage": {
            "directories": [{"path": p, "mode": m} for p, m in directories],
            "files": files,
        },
        "systemd": {"units": unit_list},
    }


def dump(cfg: dict) -> str:
    return yaml.dump(cfg, Dumper=_Dumper, sort_keys=False, default_flow_style=False, width=1000)


def write(dep: Deployment, node: Node, tree: Tree, directories: list, out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    bu = out_dir / "config.bu"
    bu.write_text(dump(config(dep, node, tree, directories)))
    return bu


def to_ignition(out_dir: Path) -> Path:
    if shutil.which("docker") is None:
        raise BuildError("docker not found in PATH; it is needed to run butane")
    ign = out_dir / "config.ign"
    if ign.exists():
        ign.unlink()
    cmd = ["docker", "run", "--rm", "-i", "--user", f"{os.getuid()}:{os.getgid()}",
           "-v", f"{out_dir}:/work", "-w", "/work", BUTANE_IMAGE,
           "--pretty", "--strict", "config.bu", "--output", "config.ign"]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise BuildError(f"butane rejected {out_dir / 'config.bu'}:\n{(r.stderr or r.stdout).strip()}")
    if not ign.is_file() or ign.stat().st_size == 0:
        raise BuildError(f"generating the ignition failed: {ign} is empty")
    return ign
