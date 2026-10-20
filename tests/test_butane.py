"""Phase 2: the butane config and the ignition."""

import os
import shutil
import subprocess
import unittest

import yaml

from builder import butane, config, rootfs

from .helpers import Scratch, node

INTEGRATION = os.environ.get("FLATCAR_BUILDER_INTEGRATION") == "1"


class Butane(Scratch):
    def bu(self, **kw):
        self.make(**kw)
        dep = config.load_deployment(self.dep)
        n = dep.nodes[0]
        return butane.config(dep, n, rootfs.full_tree(dep, n, "usb"), rootfs.directories(n)), dep, n

    def test_shape(self):
        cfg, _, _ = self.bu()
        parsed = yaml.safe_load(butane.dump(cfg))
        self.assertEqual(parsed["variant"], "flatcar")
        self.assertEqual(parsed["passwd"]["users"][0]["ssh_authorized_keys"], ["ssh-ed25519 AAAATESTKEY test@fixture"])
        self.assertNotIn("password_hash", parsed["passwd"]["users"][0])
        paths = {f["path"] for f in parsed["storage"]["files"]}
        self.assertIn("/etc/hostname", paths)
        self.assertIn("/opt/compose/compose.yml", paths)
        self.assertNotIn("/etc/systemd/system/compose-stack.service", paths)   # a unit, not a file

    def test_units_and_masks(self):
        cfg, _, _ = self.bu()
        units = {u["name"]: u for u in cfg["systemd"]["units"]}
        for name in ("compose-stack.service", "import-images.service", "watch-install.service"):
            self.assertTrue(units[name]["enabled"], name)
            self.assertIn("[Service]", units[name]["contents"])
        for name in butane.MASKED_UNITS:
            self.assertTrue(units[name]["mask"], name)

    def test_password_hash(self):
        cfg, _, _ = self.bu(deployment={"access": {"ssh_authorized_keys_files": ["keys/test.pub"],
                                                   "core_password_hash": "$6$salt$hash"}})
        self.assertEqual(cfg["passwd"]["users"][0]["password_hash"], "$6$salt$hash")

    def test_secrets_never_reach_the_ignition(self):
        n = node(secrets="secrets")
        n["apps"][0]["secrets"] = "apps/web/secrets"
        self.make(nodes={"test-node": n}, files={"nodes/test-node/secrets/etc/a/onion": "SECRET-ONION\n",
                                                  "nodes/test-node/apps/web/secrets/etc/web/key": "SECRET-API\n"})
        dep = config.load_deployment(self.dep)
        text = butane.dump(butane.config(dep, dep.nodes[0], rootfs.full_tree(dep, dep.nodes[0], "usb"),
                                         rootfs.directories(dep.nodes[0])))
        self.assertNotIn("SECRET-ONION", text)
        self.assertNotIn("SECRET-API", text)

    def test_modes_and_directories(self):
        cfg, _, _ = self.bu(nodes={"test-node": node(directories=[{"path": "/var/lib/x", "mode": "0700"}])})
        files = {f["path"]: f for f in cfg["storage"]["files"]}
        self.assertEqual(files["/opt/bin/watch-install.sh"]["mode"], 0o755)
        self.assertIn({"path": "/var/lib/x", "mode": 0o700}, cfg["storage"]["directories"])

    def test_compose_embedded_unchanged(self):
        cfg, _, n = self.bu()
        files = {f["path"]: f for f in cfg["storage"]["files"]}
        self.assertEqual(files["/opt/compose/compose.d/10-web.yml"]["contents"]["inline"],
                         n.apps[0].compose[0].read_text())

    def test_ignition_is_made_with_butane(self):
        cfg, dep, n = self.bu()
        out = self.tmp / "ign"
        butane.write(dep, n, rootfs.full_tree(dep, n, "usb"), rootfs.directories(n), out)
        env = self.env()
        old = os.environ.copy()
        os.environ.update(env)
        try:
            butane.to_ignition(out)
        finally:
            os.environ.clear()
            os.environ.update(old)
        self.assertIn("quay.io/coreos/butane:release --pretty --strict config.bu --output config.ign", self.stub_log())
        self.assertTrue((out / "config.ign").stat().st_size > 0)

    def test_empty_ignition_is_an_error(self):
        from builder.errors import BuildError
        out = self.tmp / "ign"
        out.mkdir()
        (out / "config.bu").write_text("x")
        # A docker that succeeds and writes nothing.
        fake = self.tmp / "bin"
        fake.mkdir()
        (fake / "docker").write_text("#!/bin/sh\nexit 0\n")
        (fake / "docker").chmod(0o755)
        old = os.environ.copy()
        os.environ["PATH"] = f"{fake}:{old['PATH']}"
        try:
            with self.assertRaises(BuildError) as cm:
                butane.to_ignition(out)
            self.assertIn("is empty", str(cm.exception))
        finally:
            os.environ.clear()
            os.environ.update(old)

    @unittest.skipUnless(INTEGRATION and shutil.which("docker"), "set FLATCAR_BUILDER_INTEGRATION=1 to run against real butane")
    def test_real_butane_accepts_the_example(self):
        from builder import TOOL_DIR
        dep = config.load_deployment(TOOL_DIR / "examples" / "minimal")
        n = dep.nodes[0]
        out = self.tmp / "real"
        butane.write(dep, n, rootfs.full_tree(dep, n, "usb"), rootfs.directories(n), out)
        butane.to_ignition(out)
        self.assertIn('"ignition"', (out / "config.ign").read_text())
