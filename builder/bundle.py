"""A firmware bundle for one node: a tar with install.sh at its root.

The watcher on the node extracts it and runs install.sh from the top of what
comes out, so the tar is packed from inside the bundle directory. The layout:

  install.sh            entrypoint
  MANIFEST.json         what this is, for people and tools
  bundle.env            the same, for install.sh (sourced)
  rootfs/               → /   (full: the whole tree; selected apps: only theirs)
  secrets/              → /   0600, only ever in a bundle
  directories.list      <mode>\\t<path>, created before anything is copied
  replace_dirs.list     <path>, emptied before rootfs/ is copied over them
  units.list            units to enable (full bundles)
  images/ images.list   the image tars of this node, or of the selected apps
  apps/<app>/services   the services of each app in the bundle
  apps/<app>/hooks/post-update.sh
  bin/docker-compose    (full bundles)
  updates/              OS payload + TARGET_FLATCAR_VERSION (mode os)
  lib/                  the scripts install.sh calls
  checksums.sha256      over everything above
"""

import datetime
import hashlib
import json
import os
import shutil
import tarfile
from pathlib import Path

from . import NODE_DIR, butane, compose, gitinfo, log, rootfs, tool_version, TOOL_DIR
from .config import Deployment, Node
from .errors import BuildError

LIB_SCRIPTS = ["update-app.sh", "update-os.sh", "disable-auto-updates.sh"]


def stamp() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d-%H%M")


def bundle_name(node: Node, mode: str, apps, when: str) -> str:
    part = f"-{'+'.join(apps)}" if apps else ""
    return f"{node.name}-{mode}{part}-{when}"


def _link_or_copy(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.link(src, dst)
    except OSError:
        shutil.copy2(src, dst)


def _write_env(path: Path, values: dict) -> None:
    lines = []
    for k, v in values.items():
        v = str(v)
        if "'" in v or "\n" in v:
            raise BuildError(f"bundle.env: {k} cannot contain a quote or a newline")
        lines.append(f"{k}='{v}'")
    path.write_text("\n".join(lines) + "\n")


def checksums(root: Path) -> None:
    lines = []
    for dirpath, dirs, files in os.walk(root):
        dirs.sort()
        for name in sorted(files):
            p = Path(dirpath) / name
            rel = p.relative_to(root).as_posix()
            if rel == "checksums.sha256":
                continue
            h = hashlib.sha256()
            with open(p, "rb") as f:
                for chunk in iter(lambda: f.read(1 << 20), b""):
                    h.update(chunk)
            lines.append(f"{h.hexdigest()}  {rel}")
    (root / "checksums.sha256").write_text("\n".join(lines) + "\n")


def pack(src: Path, out: Path) -> Path:
    """tar of src's CONTENTS (./install.sh, not <dir>/install.sh), owned by root."""
    out.parent.mkdir(parents=True, exist_ok=True)

    def as_root(ti: tarfile.TarInfo) -> tarfile.TarInfo:
        ti.uid = ti.gid = 0
        ti.uname = ti.gname = "root"
        return ti

    tmp = out.with_name(out.name + ".part")
    with tarfile.open(tmp, "w", format=tarfile.GNU_FORMAT) as tar:
        tar.add(src, arcname=".", filter=as_root)
    with tarfile.open(tmp) as tar:
        if "./install.sh" not in tar.getnames():
            tmp.unlink()
            raise BuildError(f"packed archive has no install.sh at its root: {out}")
    tmp.rename(out)
    return out


def build(dep: Deployment, node: Node, mode: str, apps, tars: dict, work: Path, out_dir: Path,
          when: str, flatcar_version: str, compose_bin: Path, payload: Path = None) -> Path:
    """Assemble and pack the bundle of one node; returns the tar."""
    partial = apps is not None
    chosen = [a for a in node.apps if not partial or a.name in apps]
    name = bundle_name(node, mode, apps, when)
    root = work / "bundle"
    if root.exists():
        shutil.rmtree(root)
    root.mkdir(parents=True)

    # The trees.
    tree = rootfs.apps_tree(node, apps, name) if partial else rootfs.full_tree(dep, node, name)
    secrets = rootfs.secrets_tree(node, apps)
    rootfs.check_secrets_apart(tree, secrets)
    tree.write(root / "rootfs")
    secrets.write(root / "secrets")
    if secrets.paths():
        for p in secrets.paths():
            log.info(f"    secret: {p}")

    (root / "directories.list").write_text(
        "".join(f"{mode_:04o}\t{path}\n" for path, mode_ in rootfs.directories(node, apps, platform=not partial)))
    (root / "replace_dirs.list").write_text("".join(f"{p}\n" for p in rootfs.replace_dirs(node, apps)))
    if not partial:
        (root / "units.list").write_text(
            "".join(f"{u}\n" for u in butane.ENABLED_UNITS if u != "docker.service"))

    # Images: only what this node — or these apps — use.
    image_names = sorted({img.name for app in chosen for img in app.images})
    for img in image_names:
        _link_or_copy(tars[img], root / "images" / tars[img].name)
    (root / "images.list").write_text("".join(f"{tars[i].name}\n" for i in image_names))

    for app in chosen:
        ad = root / "apps" / app.name
        ad.mkdir(parents=True)
        (ad / "services").write_text("".join(f"{s}\n" for s in compose.app_services(node, app)))
        if app.post_update:
            hook = ad / "hooks" / "post-update.sh"
            hook.parent.mkdir()
            shutil.copyfile(app.post_update, hook)
            os.chmod(hook, 0o755)

    if not partial:
        _link_or_copy(compose_bin, root / "bin" / "docker-compose")
        os.chmod(root / "bin" / "docker-compose", 0o755)

    if mode == "os":
        _link_or_copy(payload, root / "updates" / payload.name)
        (root / "updates" / "TARGET_FLATCAR_VERSION").write_text(flatcar_version + "\n")

    (root / "lib").mkdir()
    for s in LIB_SCRIPTS:
        shutil.copyfile(NODE_DIR / "lib" / s, root / "lib" / s)
        os.chmod(root / "lib" / s, 0o755)
    shutil.copyfile(NODE_DIR / "install.sh", root / "install.sh")
    os.chmod(root / "install.sh", 0o755)

    env = {
        "BUNDLE": name,
        "NODE": node.name,
        "MODE": mode,
        "PARTIAL": "1" if partial else "0",
        "APPS": " ".join(a.name for a in chosen),
    }
    if mode == "os":
        env["TARGET_FLATCAR_VERSION"] = flatcar_version
    _write_env(root / "bundle.env", env)

    manifest = {
        "format": 1,
        "bundle": name,
        "node": node.name,
        "hostname": node.hostname,
        "mode": mode,
        "partial": partial,
        "apps": [{"name": a.name,
                  "services": compose.app_services(node, a),
                  "images": [i.name for i in a.images]} for a in chosen],
        "images": [tars[i].name for i in image_names],
        "flatcar": {"channel": dep.channel, "arch": dep.arch, "version": flatcar_version},
        "built_at": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "builder": {"version": tool_version(), **gitinfo.describe(TOOL_DIR)},
        "deployment": gitinfo.describe(dep.dir),
    }
    (root / "MANIFEST.json").write_text(json.dumps(manifest, indent=2) + "\n")

    checksums(root)
    out = pack(root, out_dir / f"{name}.tar")
    log.info(f"  bundle: {out} ({out.stat().st_size // (1 << 20)} MB)")
    return out
