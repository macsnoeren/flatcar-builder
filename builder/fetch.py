"""Downloads the build needs from outside, cached.

The cache is shared by every deployment on the build machine:
  $FLATCAR_BUILDER_CACHE, else $XDG_CACHE_HOME/flatcar-builder, else ~/.cache/flatcar-builder

A download goes to <name>.part and is renamed only when it is complete, so an
interrupted one never looks like a cache hit.
"""

import os
import shutil
import time
import urllib.error
import urllib.request
from pathlib import Path

from . import log
from .errors import BuildError

RELEASE_URL = "https://{channel}.release.flatcar-linux.net/{arch}/{version}"
UPDATE_URL = "https://update.release.flatcar-linux.net/{arch}/{version}"
INSTALLER_URL = "https://raw.githubusercontent.com/flatcar/init/flatcar-master/bin/flatcar-install"
COMPOSE_URL = "https://github.com/docker/compose/releases/download/{version}/docker-compose-linux-{arch}"

IMAGE = "flatcar_production_image.bin.bz2"
PAYLOAD = "flatcar_production_update.gz"
INSTALLER = "flatcar-install"

COMPOSE_ARCH = {"amd64-usr": "x86_64", "arm64-usr": "aarch64"}


def cache_dir() -> Path:
    if os.environ.get("FLATCAR_BUILDER_CACHE"):
        return Path(os.environ["FLATCAR_BUILDER_CACHE"])
    base = os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache")
    return Path(base) / "flatcar-builder"


def _get(url: str, timeout: int = 60):
    req = urllib.request.Request(url, headers={"User-Agent": "flatcar-builder"})
    return urllib.request.urlopen(req, timeout=timeout)


def download(urls: list, dest: Path, retries: int = 3, pause: float = None) -> Path:
    """dest from the cache, or from the first URL that delivers it."""
    if pause is None:
        pause = float(os.environ.get("FLATCAR_BUILDER_RETRY_PAUSE", "2"))
    if dest.is_file():
        log.info(f"  cache hit: {dest.name}")
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")
    errors = []
    for url in urls:
        for attempt in range(1, retries + 1):
            try:
                log.info(f"  downloading: {url}" + (f" (attempt {attempt})" if attempt > 1 else ""))
                with _get(url) as r, open(part, "wb") as f:
                    shutil.copyfileobj(r, f)
                    # A connection that drops early is just a short read here,
                    # not an error; the length is the only thing that tells.
                    expected = r.headers.get("Content-Length")
                    if expected is not None and f.tell() != int(expected):
                        raise OSError(f"connection closed after {f.tell()} of {expected} bytes")
                part.rename(dest)
                return dest
            except urllib.error.HTTPError as e:
                part.unlink(missing_ok=True)
                errors.append(f"{url}: HTTP {e.code}")
                if e.code == 404:
                    break           # not there; retrying will not change that
            except (urllib.error.URLError, OSError) as e:
                part.unlink(missing_ok=True)
                errors.append(f"{url}: {getattr(e, 'reason', e)}")
            if attempt < retries:
                time.sleep(pause * attempt)
    raise BuildError(f"could not download {dest.name}. Tried:\n  " + "\n  ".join(errors))


def resolve_version(channel: str, arch: str, version: str, release_url: str = RELEASE_URL) -> str:
    if version != "current":
        return version
    url = release_url.format(channel=channel, arch=arch, version="current") + "/version.txt"
    log.info(f"Looking up the current {channel} version for {arch}")
    try:
        with _get(url, timeout=30) as r:
            text = r.read().decode()
    except (urllib.error.URLError, OSError) as e:
        raise BuildError(f"could not read {url}: {getattr(e, 'reason', e)}. "
                         "Pin flatcar.version in deployment.yml to build without looking it up") from None
    for line in text.splitlines():
        if line.startswith("FLATCAR_VERSION="):
            resolved = line.split("=", 1)[1].strip()
            log.info(f"  current => {resolved}")
            return resolved
    raise BuildError(f"no FLATCAR_VERSION in {url}")


def flatcar_image(channel, arch, version, release_url=RELEASE_URL, **kw) -> Path:
    base = release_url.format(channel=channel, arch=arch, version=version)
    return download([f"{base}/{IMAGE}"], cache_dir() / "flatcar" / arch / version / IMAGE, **kw)


def flatcar_payload(channel, arch, version, release_url=RELEASE_URL, update_url=UPDATE_URL, **kw) -> Path:
    urls = [release_url.format(channel=channel, arch=arch, version=version) + f"/{PAYLOAD}",
            update_url.format(arch=arch, version=version) + f"/{PAYLOAD}"]
    return download(urls, cache_dir() / "flatcar" / arch / version / PAYLOAD, **kw)


def flatcar_installer(url=INSTALLER_URL, **kw) -> Path:
    return download([url], cache_dir() / INSTALLER, **kw)


def compose_binary(version, arch, url=COMPOSE_URL, **kw) -> Path:
    if arch not in COMPOSE_ARCH:
        raise BuildError(f"unknown Flatcar arch {arch!r}: no docker-compose build for it")
    carch = COMPOSE_ARCH[arch]
    return download([url.format(version=version, arch=carch)],
                    cache_dir() / f"docker-compose-{version}-{carch}", **kw)
