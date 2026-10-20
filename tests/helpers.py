"""Shared test fixtures: a scratch deployment, a pre-filled cache, stubs on PATH.

Nothing here downloads anything or talks to a docker daemon: docker, systemctl
and friends are the stand-ins in tests/stubs/, and the Flatcar artefacts are
small placeholder files in a scratch cache.
"""

import copy
import os
import subprocess
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path

import yaml

TESTS = Path(__file__).resolve().parent
TOOL = TESTS.parent
STUBS = TESTS / "stubs"
VERSION = "9999.0.0"
COMPOSE = "v2.39.1"

DEPLOYMENT = {
    "builder": ">=0.1",
    "flatcar": {"channel": "stable", "version": VERSION, "arch": "amd64-usr"},
    "compose_version": COMPOSE,
    "access": {"ssh_authorized_keys_files": ["keys/test.pub"]},
}

WEB_PART = """services:
  web:
    image: nginx:1.27-alpine
    environment:
      NODE_NAME: ${NODE_NAME}
"""

NODE = {
    "install_device": "/dev/vda",
    "network": {"interfaces": [{"name": "eth0", "mode": "dhcp"}]},
    "apps": [{
        "name": "web",
        "compose": "compose.d/10-web.yml",
        "images": [{"image": "nginx:1.27-alpine", "pull": True}],
    }],
}


def node(**changes) -> dict:
    n = copy.deepcopy(NODE)
    n.update(changes)
    return n


class Scratch(unittest.TestCase):
    """A test with a scratch directory, a deployment in it and a cache next to it."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="fcb-test-")
        self.tmp = Path(self._tmp.name)
        self.dep = self.tmp / "deployment"
        self.cache = self.tmp / "cache"
        self.log = self.tmp / "stub.log"
        self.log.touch()
        self.fill_cache()

    def tearDown(self):
        self._tmp.cleanup()

    # -- deployment ---------------------------------------------------------

    def write(self, rel: str, content: str, base: Path = None) -> Path:
        p = (base or self.dep) / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
        return p

    def make(self, nodes=None, deployment=None, parts=None, files=None):
        """Write a deployment. nodes: {name: node.yml dict}; parts: {node: {rel: text}};
        files: {path relative to the deployment: text}."""
        d = copy.deepcopy(DEPLOYMENT)
        if deployment:
            d.update(deployment)
        self.write("deployment.yml", yaml.safe_dump(d, sort_keys=False))
        self.write("keys/test.pub", "ssh-ed25519 AAAATESTKEY test@fixture\n")
        nodes = nodes if nodes is not None else {"test-node": node()}
        for name, n in nodes.items():
            self.write(f"nodes/{name}/node.yml", yaml.safe_dump(n, sort_keys=False))
            node_parts = (parts or {}).get(name)
            if node_parts is None:
                node_parts = {"compose.d/10-web.yml": WEB_PART}
            for rel, text in node_parts.items():
                self.write(f"nodes/{name}/{rel}", text)
        for rel, text in (files or {}).items():
            self.write(rel, text)
        return self.dep

    def fill_cache(self):
        v = self.cache / "flatcar" / "amd64-usr" / VERSION
        v.mkdir(parents=True)
        (v / "flatcar_production_image.bin.bz2").write_text("stub image\n")
        (v / "flatcar_production_update.gz").write_text("stub payload\n")
        installer = self.cache / "flatcar-install"
        installer.write_text((STUBS / "flatcar-install").read_text())
        dc = self.cache / f"docker-compose-{COMPOSE}-x86_64"
        dc.write_text((STUBS / "docker-compose").read_text())
        os.chmod(dc, 0o755)

    # -- running ------------------------------------------------------------

    def env(self, **extra) -> dict:
        e = dict(os.environ)
        e.update({
            "PATH": f"{STUBS}:{e['PATH']}",
            "PYTHONPATH": str(TOOL),
            "FLATCAR_BUILDER_CACHE": str(self.cache),
            "STUB_LOG": str(self.log),
            "FLATCAR_BUILDER_RETRY_PAUSE": "0",
        })
        e.pop("FLATCAR_BUILDER_QUIET", None)
        e.update({k: str(v) for k, v in extra.items()})
        return e

    def build(self, *args, **env) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, "-m", "builder", str(self.dep), *args],
                              env=self.env(**env), capture_output=True, text=True, cwd=self.tmp)

    def assertBuilds(self, *args, **env) -> subprocess.CompletedProcess:
        r = self.build(*args, **env)
        self.assertEqual(r.returncode, 0, f"build failed:\n{r.stderr}")
        return r

    def assertRefused(self, fragment: str, *args, **env) -> subprocess.CompletedProcess:
        r = self.build(*args, **env)
        self.assertNotEqual(r.returncode, 0, f"build accepted it:\n{r.stderr}")
        self.assertIn(fragment, r.stderr)
        return r

    def stub_log(self) -> str:
        return self.log.read_text()

    def bundles(self) -> list:
        return sorted((self.dep / "build" / "bundles").glob("*.tar"))

    def extract(self, tar: Path, dest: Path = None) -> Path:
        dest = dest or self.tmp / ("x-" + tar.stem)
        dest.mkdir(parents=True, exist_ok=True)
        with tarfile.open(tar) as t:
            t.extractall(dest, filter="tar")
        return dest

    def run_script(self, script: Path, *args, root: Path, check=True, **env) -> subprocess.CompletedProcess:
        r = subprocess.run(["bash", str(script), *args], env=self.env(ROOT=root, **env),
                           capture_output=True, text=True)
        if check:
            self.assertEqual(r.returncode, 0, f"{script.name} failed:\n{r.stdout}\n{r.stderr}")
        return r


def load_yaml(path: Path):
    return yaml.safe_load(Path(path).read_text())
