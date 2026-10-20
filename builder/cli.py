"""build-flatcar.sh: arguments and the phases of a build.

  1. load and validate everything (all chosen nodes) — nothing built yet
  2. resolve the Flatcar version and fetch what is needed (cached)
  3. build/pull/save the images of the chosen nodes and apps, each once
  4. per node: the USB entry (usb) or the bundle (os, app)
"""

import argparse
import shutil
from pathlib import Path

from . import bundle, compose, fetch, images, log, network, rootfs, tool_version, usb
from .config import load_deployment, warn_builder_spec
from .errors import BuildError

MODES = ("usb", "os", "app")


class UsageError(Exception):
    pass


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        raise UsageError(message)


def parser() -> argparse.ArgumentParser:
    p = _Parser(
        prog="build-flatcar.sh",
        description="Build Flatcar installs and update bundles from a deployment directory.",
        epilog="usb: maintenance USB for empty machines (all apps). "
               "os: per-node bundle with the OS update and all apps. "
               "app: per-node bundle with all apps, or with --app only those.")
    p.add_argument("deployment", help="the deployment directory (with deployment.yml and nodes/)")
    p.add_argument("--mode", choices=MODES, default="usb", help="what to build (default: usb)")
    p.add_argument("--node", action="append", dest="nodes", metavar="NODE",
                   help="build only this node; may be repeated (default: all nodes)")
    p.add_argument("--app", action="append", dest="apps", metavar="APP",
                   help="with --mode app: only this app; may be repeated")
    p.add_argument("--out", metavar="DIR", help="output directory (default: <deployment>/build)")
    p.add_argument("--check", action="store_true", help="validate only: no download, no build")
    p.add_argument("--version", action="version", version=f"Flatcar Builder {tool_version()}")
    return p


def parse_args(argv: list) -> argparse.Namespace:
    args = parser().parse_args(argv)
    if args.apps and args.mode != "app":
        raise UsageError(f"--app only works with --mode app (a {args.mode} build carries every app)")
    return args


def select(dep, args) -> list:
    """[(node, [apps])] for the chosen nodes and apps."""
    if args.nodes:
        names = []
        for n in args.nodes:
            if dep.node(n) is None:
                raise BuildError(f"unknown node {n!r}; the deployment has: {', '.join(x.name for x in dep.nodes)}")
            if n not in names:
                names.append(n)
        nodes = [dep.node(n) for n in names]
    else:
        nodes = list(dep.nodes)

    selection = []
    for node in nodes:
        if args.apps:
            missing = [a for a in args.apps if node.app(a) is None]
            if missing:
                raise BuildError(f"node {node.name} has no app {', '.join(missing)} "
                                 f"(it has: {', '.join(a.name for a in node.apps) or 'none'})")
            selection.append((node, [a for a in node.apps if a.name in args.apps]))
        else:
            selection.append((node, list(node.apps)))
    return selection


def validate(dep, selection) -> None:
    network.validate_deployment(dep)
    for node, _ in selection:
        compose.validate(node)
        compose.check_images(node)
        rootfs.validate(dep, node)
    images.collect(selection)


def run(args) -> int:
    dep = load_deployment(Path(args.deployment))
    warn_builder_spec(dep, tool_version())
    selection = select(dep, args)
    log.info(f"Validating {len(selection)} node(s): {', '.join(n.name for n, _ in selection)}")
    validate(dep, selection)
    if args.check:
        log.info("Check passed; nothing was built.")
        return 0

    out = Path(args.out).resolve() if args.out else dep.dir / "build"
    work = out / "work"
    for node, _ in selection:
        if (work / node.name).exists():
            shutil.rmtree(work / node.name)

    version = dep.version
    if args.mode in ("usb", "os"):
        version = fetch.resolve_version(dep.channel, dep.arch, dep.version)

    log.info(f"Fetching (Flatcar {dep.channel} {version} {dep.arch}, compose {dep.compose_version})")
    compose_bin = fetch.compose_binary(dep.compose_version, dep.arch)
    payload = image = installer = None
    if args.mode == "os":
        payload = fetch.flatcar_payload(dep.channel, dep.arch, version)
    if args.mode == "usb":
        image = fetch.flatcar_image(dep.channel, dep.arch, version)
        installer = fetch.flatcar_installer()

    wanted = images.collect(selection)
    log.info(f"Images: {len(wanted)} unique")
    tars = images.produce(wanted, work / "images")

    if args.mode == "usb":
        log.info("Building the USB")
        dest = usb.build(dep, [n for n, _ in selection], tars, out / "usb", work, version,
                         image, installer, compose_bin)
        log.info(f"Done: {dest}")
        return 0

    when = bundle.stamp()
    built = []
    for node, _ in selection:
        log.info(f"Bundle for {node.name}")
        built.append(bundle.build(dep, node, args.mode, args.apps, tars, work / node.name,
                                  out / "bundles", when, version, compose_bin, payload))
    log.info("Done:")
    for b in built:
        log.info(f"  {b}")
    return 0


def main(argv: list) -> int:
    try:
        args = parse_args(argv)
    except UsageError as e:
        parser().print_usage()
        log.error(str(e))
        return 2
    except SystemExit as e:          # --help, --version
        return int(e.code or 0)
    try:
        return run(args)
    except BuildError as e:
        log.error(str(e))
        return 1
    except KeyboardInterrupt:
        log.error("interrupted")
        return 130
