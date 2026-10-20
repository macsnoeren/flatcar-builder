"""Reading deployment.yml and nodes/<name>/node.yml into checked objects.

This module checks shape and types and resolves paths. What the values MEAN
together — network, diode links, compose, overlays — is checked by the modules
that use them, all of which run before anything is built (see cli.validate).
"""

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import yaml

from . import log
from .errors import ConfigError
from .schema import (Ctx, as_list, as_map, get_bool, get_int, get_mode, get_str,
                     get_str_list, local_dir, local_file, local_path, node_path)

NAME_RE = r"[A-Za-z0-9][A-Za-z0-9._-]*"
APP_RE = r"[a-z0-9][a-z0-9_-]*"
IFACE_RE = r"[A-Za-z0-9][A-Za-z0-9_.-]*"
SIZE_RE = r"\d+[kKmMgG]?"
DURATION_RE = r"\d+(s|m|h)?"

CHANNELS = ("stable", "beta", "alpha", "lts")
ARCHES = ("amd64-usr", "arm64-usr")
NET_MODES = ("dhcp", "dhcp4", "dhcp6", "static")
NET_OPTIONS = ("onlink", "ra", "nocarrier")


@dataclass
class Image:
    name: str
    pull: bool
    context: Optional[Path] = None
    dockerfile: Optional[Path] = None
    args: dict = field(default_factory=dict)
    where: str = ""

    def definition(self) -> tuple:
        """What has to be equal for two mentions of this image to be the same image."""
        if self.pull:
            return ("pull",)
        return ("build", str(self.context), str(self.dockerfile or ""), tuple(self.args.items()))

    def describe(self) -> str:
        if self.pull:
            return "pull"
        s = f"build {self.context}"
        if self.dockerfile:
            s += f" -f {self.dockerfile}"
        for k, v in self.args.items():
            s += f" --build-arg {k}={v}"
        return s

    @property
    def tar_name(self) -> str:
        return tar_name_for(self.name)


def tar_name_for(image: str) -> str:
    """"name:tag" -> "name_tag.tar"; a registry path's slashes become underscores too."""
    return image.replace("/", "_").replace(":", "_") + ".tar"


@dataclass
class Directory:
    path: str
    mode: int
    where: str


@dataclass
class App:
    name: str
    compose: list
    images: list
    directories: list
    files: Optional[Path]
    secrets: Optional[Path]
    replace_dirs: list
    post_update: Optional[Path]
    where: str


@dataclass
class Interface:
    name: str
    mode: Optional[str]
    addresses: list
    gateways: list
    dns: list
    options: list
    match_permanent_mac: Optional[str]
    mac: Optional[str]
    arp: Optional[bool]
    where: str


@dataclass
class Node:
    name: str
    dir: Path
    hostname: str
    install_device: str
    interfaces: list
    project: str
    files: Optional[Path]
    secrets: Optional[Path]
    directories: list
    apps: list

    def app(self, name: str) -> Optional[App]:
        return next((a for a in self.apps if a.name == name), None)

    def interface(self, name: str) -> Optional[Interface]:
        return next((i for i in self.interfaces if i.name == name), None)


@dataclass
class DiodeEnd:
    node: str
    interface: str
    address: str
    mac: Optional[str]
    where: str


@dataclass
class DiodeLink:
    name: str
    sender: DiodeEnd
    receiver: DiodeEnd
    where: str


@dataclass
class Deployment:
    dir: Path
    builder_spec: Optional[str]
    channel: str
    version: str
    arch: str
    compose_version: str
    ssh_keys: list
    core_password_hash: str
    docker_log_max_size: str
    docker_log_max_file: int
    journal_max_use: str
    journal_max_file_size: str
    os_boot_check_timeout: str
    common_files: Optional[Path]
    nodes: list
    diode_links: list

    def node(self, name: str) -> Optional[Node]:
        return next((n for n in self.nodes if n.name == name), None)


def read_yaml(path: Path, label: str):
    try:
        with open(path) as f:
            return yaml.safe_load(f)
    except yaml.YAMLError as e:
        raise ConfigError(label, f"not valid YAML: {e}") from None
    except OSError as e:
        raise ConfigError(label, f"cannot be read: {e.strerror}") from None


# ---------------------------------------------------------------------------
# deployment.yml
# ---------------------------------------------------------------------------

DEPLOYMENT_KEYS = {"builder", "flatcar", "compose_version", "access", "logs",
                   "os_boot_check", "common_files", "nodes", "diode_links"}


def load_deployment(deploy_dir: Path) -> Deployment:
    deploy_dir = Path(deploy_dir).resolve()
    if not deploy_dir.is_dir():
        raise ConfigError(str(deploy_dir), "deployment directory not found")
    path = deploy_dir / "deployment.yml"
    if not path.is_file():
        raise ConfigError(str(deploy_dir), "has no deployment.yml")

    ctx = Ctx("deployment.yml")
    d = as_map(read_yaml(path, ctx.file), ctx, DEPLOYMENT_KEYS,
               required=("flatcar", "compose_version", "access"))

    builder_spec = get_str(d, "builder", ctx, default=None)

    fc = ctx.child("flatcar")
    f = as_map(d["flatcar"], fc, {"channel", "version", "arch"}, required=("channel", "version"))
    channel = get_str(f, "channel", fc, choices=CHANNELS)
    version = get_str(f, "version", fc, pattern=r"current|\d+\.\d+\.\d+")
    arch = get_str(f, "arch", fc, default="amd64-usr", choices=ARCHES)

    compose_version = get_str(d, "compose_version", ctx, pattern=r"v\d+\.\d+\.\d+")

    ac = ctx.child("access")
    a = as_map(d["access"], ac, {"ssh_authorized_keys_files", "core_password_hash"},
               required=("ssh_authorized_keys_files",))
    key_files = get_str_list(a, "ssh_authorized_keys_files", ac)
    if not key_files:
        raise ac.child("ssh_authorized_keys_files").fail(
            "needs at least one key: on a VPS SSH is the only way in")
    ssh_keys = []
    for i, kf in enumerate(key_files):
        p = local_path(kf, deploy_dir)
        if not p.is_file():
            raise ac.child("ssh_authorized_keys_files").item(i).fail(
                f"{p} not found (generate one with: ssh-keygen -t ed25519 -a 100 -f {p.with_suffix('')})")
        for line in p.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                ssh_keys.append(line)
    core_password_hash = get_str(a, "core_password_hash", ac, default="")
    if core_password_hash and not core_password_hash.startswith("$"):
        raise ac.child("core_password_hash").fail(
            "does not look like a crypt hash (must start with '$', e.g. $6$...). "
            "Generate one with: openssl passwd -6 — never put a plaintext password here")

    lc = ctx.child("logs")
    lg = as_map(d.get("logs"), lc, {"docker", "journal"})
    dc = lc.child("docker")
    ld = as_map(lg.get("docker"), dc, {"max_size", "max_file"})
    jc = lc.child("journal")
    lj = as_map(lg.get("journal"), jc, {"max_use", "max_file_size"})

    oc = ctx.child("os_boot_check")
    ob = as_map(d.get("os_boot_check"), oc, {"timeout"})

    common_files = local_dir(d, "common_files", ctx, deploy_dir)

    nodes = _load_nodes(deploy_dir, d, ctx)

    links = []
    lnc = ctx.child("diode_links")
    for i, raw in enumerate(as_list(d.get("diode_links"), lnc)):
        links.append(_load_link(raw, lnc.item(i)))

    return Deployment(
        dir=deploy_dir,
        builder_spec=builder_spec,
        channel=channel,
        version=version,
        arch=arch,
        compose_version=compose_version,
        ssh_keys=ssh_keys,
        core_password_hash=core_password_hash,
        docker_log_max_size=get_str(ld, "max_size", dc, default="10m", pattern=SIZE_RE),
        docker_log_max_file=get_int(ld, "max_file", dc, default=3, minimum=1),
        journal_max_use=get_str(lj, "max_use", jc, default="200M", pattern=SIZE_RE),
        journal_max_file_size=get_str(lj, "max_file_size", jc, default="20M", pattern=SIZE_RE),
        os_boot_check_timeout=get_str(ob, "timeout", oc, default="10m", pattern=DURATION_RE),
        common_files=common_files,
        nodes=nodes,
        diode_links=links,
    )


def _load_nodes(deploy_dir: Path, d: dict, ctx: Ctx) -> list:
    nodes_dir = deploy_dir / "nodes"
    if "nodes" in d and d["nodes"] is not None:
        names = get_str_list(d, "nodes", ctx)
        if not names:
            raise ctx.child("nodes").fail("is empty")
        seen = set()
        for i, n in enumerate(names):
            if n in seen:
                raise ctx.child("nodes").item(i).fail(f"node {n!r} is listed twice")
            seen.add(n)
            if not (nodes_dir / n).is_dir():
                raise ctx.child("nodes").item(i).fail(f"no directory nodes/{n}")
    else:
        if not nodes_dir.is_dir():
            raise ctx.fail("there is no nodes/ directory next to it")
        names = sorted(p.name for p in nodes_dir.iterdir() if p.is_dir() and not p.name.startswith("."))
        if not names:
            raise ConfigError("nodes/", "contains no node directories")
    return [load_node(nodes_dir / n) for n in names]


def _load_link(raw, ctx: Ctx) -> DiodeLink:
    d = as_map(raw, ctx, {"name", "from", "to"}, required=("name", "from", "to"))
    name = get_str(d, "name", ctx, pattern=APP_RE)

    def end(key, needs_mac):
        c = ctx.child(key)
        allowed = {"node", "interface", "address"} | ({"mac"} if needs_mac else set())
        e = as_map(d[key], c, allowed, required=("node", "interface", "address") + (("mac",) if needs_mac else ()))
        return DiodeEnd(
            node=get_str(e, "node", c),
            interface=get_str(e, "interface", c),
            address=get_str(e, "address", c),
            mac=get_str(e, "mac", c, default=None),
            where=c.where,
        )

    # The receiver carries the MAC: it is the address the sender writes into its
    # static neighbour entry, so it has to be fixed on the receiving card.
    return DiodeLink(name=name, sender=end("from", False), receiver=end("to", True), where=ctx.where)


# ---------------------------------------------------------------------------
# nodes/<name>/node.yml
# ---------------------------------------------------------------------------

NODE_KEYS = {"hostname", "install_device", "network", "compose", "files", "secrets",
             "directories", "apps"}


def load_node(node_dir: Path) -> Node:
    name = node_dir.name
    label = f"nodes/{name}/node.yml"
    ctx = Ctx(label)
    if not re.fullmatch(NAME_RE, name):
        raise ConfigError(f"nodes/{name}", "is not a usable node name (letters, digits, '.', '_', '-')")
    path = node_dir / "node.yml"
    if not path.is_file():
        raise ConfigError(f"nodes/{name}", "has no node.yml")

    d = as_map(read_yaml(path, label), ctx, NODE_KEYS, required=("install_device", "network"))

    hostname = get_str(d, "hostname", ctx, default=name, pattern=r"[A-Za-z0-9]([A-Za-z0-9.-]*[A-Za-z0-9])?")
    install_device = get_str(d, "install_device", ctx, pattern=r"/dev/[A-Za-z0-9/_-]+")

    nc = ctx.child("network")
    n = as_map(d["network"], nc, {"interfaces"}, required=("interfaces",))
    ic = nc.child("interfaces")
    interfaces = [_load_interface(raw, ic.item(i)) for i, raw in enumerate(as_list(n["interfaces"], ic))]

    cc = ctx.child("compose")
    c = as_map(d.get("compose"), cc, {"project"})
    project = get_str(c, "project", cc, default=_project_name(name), pattern=r"[a-z0-9][a-z0-9_-]*")

    directories = _load_directories(d, ctx)

    ac = ctx.child("apps")
    apps = [_load_app(raw, ac.item(i), node_dir) for i, raw in enumerate(as_list(d.get("apps"), ac))]
    seen = {}
    for i, app in enumerate(apps):
        if app.name in seen:
            raise ac.item(i).fail(f"app {app.name!r} is defined twice (also at apps[{seen[app.name]}])")
        seen[app.name] = i

    return Node(
        name=name,
        dir=node_dir.resolve(),
        hostname=hostname,
        install_device=install_device,
        interfaces=interfaces,
        project=project,
        files=local_dir(d, "files", ctx, node_dir),
        secrets=local_dir(d, "secrets", ctx, node_dir),
        directories=directories,
        apps=apps,
    )


def _project_name(node_name: str) -> str:
    """docker compose wants a lower-case project name of [a-z0-9_-]."""
    return re.sub(r"[^a-z0-9_-]", "-", node_name.lower()).strip("-_") or "stack"


def _load_directories(d: dict, ctx: Ctx) -> list:
    dc = ctx.child("directories")
    out = []
    for i, raw in enumerate(as_list(d.get("directories"), dc)):
        c = dc.item(i)
        e = as_map(raw, c, {"path", "mode"}, required=("path", "mode"))
        out.append(Directory(path=node_path(get_str(e, "path", c), c.child("path")),
                             mode=get_mode(e, "mode", c), where=c.where))
    return out


def _load_interface(raw, ctx: Ctx) -> Interface:
    d = as_map(raw, ctx, {"name", "mode", "addresses", "gateways", "dns", "options",
                          "match_permanent_mac", "mac", "arp"})
    name = get_str(d, "name", ctx, default="")
    if name and not re.fullmatch(IFACE_RE, name):
        raise ctx.child("name").fail(f"{name!r} is not a usable interface name")
    return Interface(
        name=name,
        mode=get_str(d, "mode", ctx, default=None),
        addresses=get_str_list(d, "addresses", ctx),
        gateways=get_str_list(d, "gateways", ctx),
        dns=get_str_list(d, "dns", ctx),
        options=get_str_list(d, "options", ctx),
        match_permanent_mac=get_str(d, "match_permanent_mac", ctx, default=None),
        mac=get_str(d, "mac", ctx, default=None),
        arp=get_bool(d, "arp", ctx, default=None),
        where=ctx.where,
    )


APP_KEYS = {"name", "compose", "images", "directories", "files", "secrets",
            "replace_dirs", "hooks"}


def _load_app(raw, ctx: Ctx, node_dir: Path) -> App:
    d = as_map(raw, ctx, APP_KEYS, required=("name", "compose"))
    name = get_str(d, "name", ctx, pattern=APP_RE)

    cc = ctx.child("compose")
    compose_raw = d["compose"]
    compose_list = [compose_raw] if isinstance(compose_raw, str) else as_list(compose_raw, cc)
    if not compose_list:
        raise cc.fail("names no compose file")
    compose = []
    for i, cf in enumerate(compose_list):
        c = cc if isinstance(compose_raw, str) else cc.item(i)
        if not isinstance(cf, str):
            raise c.fail("expected a path to a compose file")
        p = local_path(cf, node_dir)
        if not p.is_file():
            raise c.fail(f"{p} not found")
        if p.suffix not in (".yml", ".yaml"):
            raise c.fail(f"{p.name} must end in .yml or .yaml")
        compose.append(p)

    imc = ctx.child("images")
    images = [_load_image(raw, imc.item(i), node_dir) for i, raw in enumerate(as_list(d.get("images"), imc))]
    seen = set()
    for i, img in enumerate(images):
        if img.name in seen:
            raise imc.item(i).fail(f"image {img.name!r} is listed twice in this app")
        seen.add(img.name)

    rc = ctx.child("replace_dirs")
    replace_dirs = []
    for i, p in enumerate(get_str_list(d, "replace_dirs", ctx)):
        p = node_path(p, rc.item(i))
        # Emptied with rm -rf on the node: /etc or /var is one typo away from a
        # node without its configuration.
        if p.count("/") < 2:
            raise rc.item(i).fail(f"{p} is too close to / to be replaced as a whole; name a directory "
                                  "at least two levels deep, e.g. /etc/myapp/conf.d")
        replace_dirs.append(p)

    hc = ctx.child("hooks")
    h = as_map(d.get("hooks"), hc, {"post_update"})

    return App(
        name=name,
        compose=compose,
        images=images,
        directories=_load_directories(d, ctx),
        files=local_dir(d, "files", ctx, node_dir),
        secrets=local_dir(d, "secrets", ctx, node_dir),
        replace_dirs=replace_dirs,
        post_update=local_file(h, "post_update", hc, node_dir),
        where=ctx.where,
    )


def _load_image(raw, ctx: Ctx, node_dir: Path) -> Image:
    d = as_map(raw, ctx, {"image", "build", "pull"}, required=("image",))
    name = get_str(d, "image", ctx, pattern=r"[^\s]+")
    pull = get_bool(d, "pull", ctx, default=False)
    if pull and d.get("build") is not None:
        raise ctx.fail(f"image {name!r} has both 'build' and 'pull: true'; it is one or the other")
    if not pull and d.get("build") is None:
        raise ctx.fail(f"image {name!r} needs either 'build' or 'pull: true'")
    if pull:
        return Image(name=name, pull=True, where=ctx.where)

    bc = ctx.child("build")
    b = as_map(d["build"], bc, {"context", "dockerfile", "args"}, required=("context",))
    context = local_path(get_str(b, "context", bc), node_dir)
    if not context.is_dir():
        raise bc.child("context").fail(f"{context} is not a directory")
    if not any(context.iterdir()):
        raise bc.child("context").fail(f"{context} is empty — an uninitialised git submodule? "
                                       "Run: git submodule update --init --recursive")
    dockerfile = None
    if b.get("dockerfile"):
        dockerfile = local_path(get_str(b, "dockerfile", bc), node_dir)
        if not dockerfile.is_file():
            raise bc.child("dockerfile").fail(f"{dockerfile} not found")
    elif not (context / "Dockerfile").is_file():
        raise bc.child("context").fail(f"{context} has no Dockerfile; name one with 'dockerfile:'")

    argc = bc.child("args")
    args = {}
    for k, v in as_map(b.get("args"), argc).items():
        if isinstance(v, bool) or not isinstance(v, (str, int, float)):
            raise argc.child(str(k)).fail(f"expected a string value, got {type(v).__name__}")
        args[str(k)] = str(v)
    return Image(name=name, pull=False, context=context, dockerfile=dockerfile, args=args, where=ctx.where)


def warn_builder_spec(dep: Deployment, version: str) -> None:
    from .version import satisfies
    if dep.builder_spec is None:
        log.warn(f"deployment.yml has no 'builder:' range; add e.g. builder: \">={version.split('.')[0]}.0\" "
                 "so a bundle cannot be built with a tool this deployment was never written for")
        return
    if not satisfies(version, dep.builder_spec):
        raise ConfigError("deployment.yml: builder",
                          f"this deployment needs Flatcar Builder {dep.builder_spec}, this is {version}")
