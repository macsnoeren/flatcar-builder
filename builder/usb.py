"""The maintenance USB: everything to install the chosen nodes on an EMPTY machine.

flatcar-install repartitions the whole disk, so this is for a first install
only. An installed node is updated with an os bundle (A/B), never from here.
The USB carries no secrets: it is not encrypted, and a node gets its secrets
with its first app bundle.
"""

import datetime
import os
import shutil
from pathlib import Path

from . import NODE_DIR, butane, fetch, rootfs, tool_version
from .bundle import _link_or_copy, checksums
from .config import Deployment


def build(dep: Deployment, nodes: list, tars: dict, out: Path, work: Path, flatcar_version: str,
          image: Path, installer: Path, compose_bin: Path, make_ignition=True) -> Path:
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)

    _link_or_copy(image, out / "flatcar" / fetch.IMAGE)
    shutil.copyfile(installer, out / "flatcar" / fetch.INSTALLER)
    os.chmod(out / "flatcar" / fetch.INSTALLER, 0o755)
    _link_or_copy(compose_bin, out / "bin" / "docker-compose")
    os.chmod(out / "bin" / "docker-compose", 0o755)

    for node in nodes:
        nd = out / "nodes" / node.name
        tree = rootfs.full_tree(dep, node, bundle="usb")
        ign_dir = work / node.name / "ignition"
        if ign_dir.exists():
            shutil.rmtree(ign_dir)
        butane.write(dep, node, tree, rootfs.directories(node), ign_dir)
        if make_ignition:
            butane.to_ignition(ign_dir)
        nd.mkdir(parents=True)
        for f in ("config.bu", "config.ign"):
            if (ign_dir / f).exists():
                shutil.copyfile(ign_dir / f, nd / f)
        (nd / "node.env").write_text(f"HOSTNAME='{node.hostname}'\nINSTALL_DEVICE='{node.install_device}'\n")
        names = sorted({img.name for app in node.apps for img in app.images})
        (nd / "images.list").write_text("".join(f"{tars[n].name}\n" for n in names))
        for n in names:
            dst = out / "docker-images" / tars[n].name
            if not dst.exists():
                _link_or_copy(tars[n], dst)

    (out / "scripts").mkdir()
    shutil.copyfile(NODE_DIR / "lib" / "install-flatcar.sh", out / "scripts" / "install-flatcar.sh")
    os.chmod(out / "scripts" / "install-flatcar.sh", 0o755)

    when = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    (out / "README.txt").write_text(
        "Flatcar maintenance USB\n"
        "=======================\n\n"
        f"Built by Flatcar Builder {tool_version()} on {when}\n"
        f"Flatcar {dep.channel} {flatcar_version} ({dep.arch})\n\n"
        "Nodes:\n" + "".join(f"  - {n.name}\n" for n in nodes) + "\n"
        "Install a node on an EMPTY machine (this wipes the whole disk), as root,\n"
        "from a rescue system or a live USB:\n\n"
        "  bash scripts/install-flatcar.sh <node> [device]\n\n"
        "Then reboot. The node has no secrets until its first app bundle.\n"
        "An installed node is updated with an os/app bundle, never from here.\n")

    checksums(out)
    return out
