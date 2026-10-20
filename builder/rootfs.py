"""The tree a node gets on '/': one source, two channels.

The same tree goes into the ignition at install time and into every full bundle,
so a changed unit, log cap or vhost reaches a running node the same way it
reaches a new one. Secrets are a separate tree that only bundles carry: the
ignition ends up in the clear on the installer USB.

Layers, in order:

  platform   node/rootfs/ of this tool + what it generates (log caps, update.conf)
  common     deployment.yml: common_files
  node       node.yml: files
  app        node.yml: apps[].files
  generated  hostname, network, node.env, the compose stack, the app records

A path in two layers is an error — two people think they own the same file —
except for the platform files in OVERRIDABLE, which exist to be replaced.
"""

import json
import os
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from . import NODE_DIR, compose, network
from .config import Deployment, Node
from .errors import BuildError, ConfigError

STATE_DIR = "/var/lib/flatcar-builder"
APPS_STATE_DIR = f"{STATE_DIR}/apps"
ENV_FILE = "/etc/flatcar-builder/node.env"
DAEMON_JSON = "/etc/docker/daemon.json"
JOURNALD_CONF = "/etc/systemd/journald.conf.d/10-size.conf"

OVERRIDABLE = {DAEMON_JSON, JOURNALD_CONF}

PLATFORM_DIRS = [
    ("/opt/images", 0o755),
    ("/opt/compose", 0o755),
    ("/opt/compose/compose.d", 0o755),
    ("/opt/bin", 0o755),
    ("/opt/deploy", 0o755),
    ("/etc/flatcar-builder", 0o755),
    (STATE_DIR, 0o755),
    (APPS_STATE_DIR, 0o755),
]

# Directories a full bundle replaces as a whole: a part or an app dropped from
# node.yml has to disappear from the node too.
PLATFORM_REPLACE_DIRS = ["/opt/compose/compose.d", APPS_STATE_DIR]

SKIP_NAMES = {"README.md"}


@dataclass
class Entry:
    layer: str
    mode: int
    source: Optional[Path] = None
    content: Optional[bytes] = None

    def data(self) -> bytes:
        return self.content if self.content is not None else self.source.read_bytes()

    def origin(self) -> str:
        return f"{self.layer} ({self.source})" if self.source else self.layer


class Tree:
    def __init__(self):
        self.entries = {}

    def add(self, path: str, entry: Entry) -> None:
        old = self.entries.get(path)
        if old is not None:
            if not (old.layer == "platform" and path in OVERRIDABLE):
                raise ConfigError(path, f"is shipped twice: by {old.origin()} and by {entry.origin()}")
        self.entries[path] = entry

    def add_text(self, path: str, text: str, layer: str, mode: int = 0o644) -> None:
        self.add(path, Entry(layer=layer, mode=mode, content=text.encode()))

    def add_overlay(self, src: Path, layer: str, secret: bool = False) -> None:
        for rel, p in walk_overlay(src):
            mode = 0o600 if secret else (0o755 if os.access(p, os.X_OK) else 0o644)
            self.add("/" + rel, Entry(layer=layer, mode=mode, source=p))

    def paths(self) -> list:
        return sorted(self.entries)

    def write(self, dest: Path) -> None:
        for path in self.paths():
            e = self.entries[path]
            target = dest / path.lstrip("/")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(e.data())
            os.chmod(target, e.mode)


def walk_overlay(src: Path):
    """(relative path, file) for every file under src; dotfiles and README.md stay behind."""
    for root, dirs, files in os.walk(src):
        dirs[:] = sorted(d for d in dirs if not d.startswith("."))
        for d in dirs:
            if os.path.islink(os.path.join(root, d)):
                raise BuildError(f"{os.path.join(root, d)} is a symlink; overlays hold plain files only")
        for name in sorted(files):
            if name.startswith(".") or name in SKIP_NAMES:
                continue
            p = Path(root) / name
            if p.is_symlink() or not stat.S_ISREG(p.lstat().st_mode):
                raise BuildError(f"{p} is not a plain file; overlays hold plain files only")
            yield str(p.relative_to(src)), p


# ---------------------------------------------------------------------------
# Generated platform files
# ---------------------------------------------------------------------------

def daemon_json(dep: Deployment) -> str:
    return json.dumps({
        "log-driver": "json-file",
        "log-opts": {"max-size": dep.docker_log_max_size, "max-file": str(dep.docker_log_max_file)},
    }, indent=2) + "\n"


def journald_conf(dep: Deployment) -> str:
    return f"[Journal]\nSystemMaxUse={dep.journal_max_use}\nSystemMaxFileSize={dep.journal_max_file_size}\n"


def update_conf(dep: Deployment) -> str:
    # SERVER=disabled is what update-engine reads as "no update server": the
    # only OS update path is a bundle this node verified. REBOOT_STRATEGY=off
    # keeps locksmithd from rebooting on its own.
    return f"GROUP={dep.channel}\nSERVER=disabled\nREBOOT_STRATEGY=off\n"


def node_env(node: Node) -> str:
    """Read by systemd (EnvironmentFile=) AND sourced by bash, so only a quoting
    both read the same way: single quotes, which neither expands."""
    values = {"NODE_NAME": node.name, "HOSTNAME": node.hostname}
    lines = []
    for k, v in values.items():
        if "'" in v or "\n" in v:
            raise ConfigError(f"nodes/{node.name}", f"{k} {v!r} cannot contain a quote or a newline")
        lines.append(f"{k}='{v}'")
    return "\n".join(lines) + "\n"


def app_record(node: Node, app, bundle: str) -> str:
    return json.dumps({
        "app": app.name,
        "node": node.name,
        "bundle": bundle,
        "compose": [p.name for p in app.compose],
        "services": compose.app_services(node, app),
        "images": [img.name for img in app.images],
    }, indent=2) + "\n"


# ---------------------------------------------------------------------------
# Assembly
# ---------------------------------------------------------------------------

def platform_layer(tree: Tree, dep: Deployment) -> None:
    tree.add_overlay(NODE_DIR / "rootfs", "platform")
    tree.add_text(DAEMON_JSON, daemon_json(dep), "platform")
    tree.add_text(JOURNALD_CONF, journald_conf(dep), "platform")
    tree.add_text("/etc/flatcar/update.conf", update_conf(dep), "platform")


def app_layer(tree: Tree, node: Node, app, bundle: str) -> None:
    """Everything of one app: its files, its compose parts, its record."""
    if app.files:
        tree.add_overlay(app.files, f"app {app.name}")
    for p in app.compose:
        tree.add(f"{compose.COMPOSE_DIR}/compose.d/{p.name}", Entry(layer=f"app {app.name}", mode=0o644, source=p))
    tree.add_text(f"{APPS_STATE_DIR}/{app.name}.json", app_record(node, app, bundle), f"app {app.name}")


def full_tree(dep: Deployment, node: Node, bundle: str) -> Tree:
    tree = Tree()
    platform_layer(tree, dep)
    if dep.common_files:
        tree.add_overlay(dep.common_files, "common")
    if node.files:
        tree.add_overlay(node.files, "node")
    for app in node.apps:
        app_layer(tree, node, app, bundle)
    tree.add_text("/etc/hostname", node.hostname + "\n", "generated")
    tree.add_text(ENV_FILE, node_env(node), "generated")
    for path, body in network.network_files(node, dep).items():
        tree.add_text(path, body, "generated")
    tree.add_text(f"{compose.COMPOSE_DIR}/compose.yml", compose.root_yaml(node), "generated")
    return tree


def apps_tree(node: Node, apps: list, bundle: str) -> Tree:
    tree = Tree()
    for app in node.apps:
        if app.name in apps:
            app_layer(tree, node, app, bundle)
    return tree


def secrets_tree(node: Node, apps=None) -> Tree:
    """Node secrets (full bundles only, apps=None) and the secrets of the apps."""
    tree = Tree()
    if apps is None and node.secrets:
        tree.add_overlay(node.secrets, "node secrets", secret=True)
    for app in node.apps:
        if app.secrets and (apps is None or app.name in apps):
            tree.add_overlay(app.secrets, f"app {app.name} secrets", secret=True)
    return tree


def check_secrets_apart(files: Tree, secrets: Tree) -> None:
    for path in secrets.paths():
        if path in files.entries:
            raise ConfigError(path, f"is both a file ({files.entries[path].origin()}) and a secret "
                                    f"({secrets.entries[path].origin()})")


def directories(node: Node, apps=None, platform=True) -> list:
    """[(path, mode)] sorted; the same path twice is fine, with two modes it is not."""
    found = {}
    sources = {}

    def add(path, mode, where):
        if path in found and found[path] != mode:
            raise ConfigError(where, f"directory {path} has mode {mode:04o} here and {found[path]:04o} "
                                     f"at {sources[path]}")
        found[path] = mode
        sources[path] = where

    if platform:
        for path, mode in PLATFORM_DIRS:
            add(path, mode, "platform")
        for d in node.directories:
            add(d.path, d.mode, d.where)
    for app in node.apps:
        if apps is None or app.name in apps:
            for d in app.directories:
                add(d.path, d.mode, d.where)
    return sorted(found.items())


def replace_dirs(node: Node, apps=None) -> list:
    out = list(PLATFORM_REPLACE_DIRS) if apps is None else []
    for app in node.apps:
        if apps is None or app.name in apps:
            out.extend(app.replace_dirs)
    return sorted(set(out))


def validate(dep: Deployment, node: Node) -> None:
    """Build both trees once, so a conflict is found by --check and not halfway a build."""
    files = full_tree(dep, node, bundle="check")
    secrets = secrets_tree(node)
    check_secrets_apart(files, secrets)
    directories(node)
