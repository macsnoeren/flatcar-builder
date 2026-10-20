"""Docker images: collected over the chosen nodes and apps, each built or pulled
once, and saved as a tar. The node never reaches a registry; the tars are what
it gets.
"""

import shutil
import subprocess
from pathlib import Path

from . import log
from .errors import BuildError


def collect(selection: list) -> dict:
    """{image name: Image} over [(node, [apps])]; the same name with two definitions is an error.

    Two nodes that build the same image name from the same context but with a
    different build argument would otherwise silently share whichever one was
    built first.
    """
    found = {}
    for node, apps in selection:
        for app in apps:
            for img in app.images:
                old = found.get(img.name)
                if old is None:
                    found[img.name] = img
                elif old.definition() != img.definition():
                    raise BuildError(f"image {img.name} is defined twice, differently:\n"
                                     f"  {old.where}: {old.describe()}\n"
                                     f"  {img.where}: {img.describe()}")
    return found


def build_command(img) -> list:
    cmd = ["docker", "build"]
    for k, v in img.args.items():
        cmd += ["--build-arg", f"{k}={v}"]
    cmd += ["-t", img.name]
    if img.dockerfile:
        cmd += ["-f", str(img.dockerfile)]
    cmd.append(str(img.context))
    return cmd


def _run(cmd: list) -> None:
    r = subprocess.run(cmd)
    if r.returncode != 0:
        raise BuildError(f"failed ({r.returncode}): {' '.join(cmd)}")


def produce(images: dict, pool: Path) -> dict:
    """Build/pull and save every image; {image name: tar path}."""
    if images and shutil.which("docker") is None:
        raise BuildError("docker not found in PATH; it is needed to build, pull and save images")
    pool.mkdir(parents=True, exist_ok=True)
    tars = {}
    for name in sorted(images):
        img = images[name]
        if img.pull:
            log.info(f"  pull  {name}")
            _run(["docker", "pull", name])
        else:
            # Build arguments are baked into the image and readable in 'docker
            # history', so they are never the place for a secret — logging them
            # says so out loud.
            if img.args:
                log.info("  args  " + " ".join(f"--build-arg {k}={v}" for k, v in img.args.items()))
            log.info(f"  build {name} <= {img.context}" + (f" (-f {img.dockerfile})" if img.dockerfile else ""))
            _run(build_command(img))
        out = pool / img.tar_name
        log.info(f"  save  {name} => {out.name}")
        _run(["docker", "save", name, "-o", str(out)])
        tars[name] = out
    return tars
