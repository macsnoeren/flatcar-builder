"""Phase 2: the USB and install-flatcar.sh."""

import json

from .helpers import Scratch, load_yaml, node


class Usb(Scratch):
    def setUp(self):
        super().setUp()
        n = node(secrets="secrets")
        self.make(nodes={"node-a": n, "node-b": node()},
                  files={"nodes/node-a/secrets/etc/a/key": "SECRET-KEY\n"})
        self.assertBuilds("--mode", "usb")
        self.usb = self.dep / "build" / "usb"

    def test_layout(self):
        for rel in ("flatcar/flatcar_production_image.bin.bz2", "flatcar/flatcar-install", "bin/docker-compose",
                    "scripts/install-flatcar.sh", "checksums.sha256", "README.txt",
                    "nodes/node-a/config.bu", "nodes/node-a/config.ign", "nodes/node-a/node.env",
                    "nodes/node-a/images.list", "nodes/node-b/config.ign",
                    "docker-images/nginx_1.27-alpine.tar"):
            self.assertTrue((self.usb / rel).is_file(), rel)
        self.assertEqual((self.usb / "nodes/node-a/images.list").read_text(), "nginx_1.27-alpine.tar\n")

    def test_checksums_match(self):
        import subprocess
        r = subprocess.run(["sha256sum", "--quiet", "-c", "checksums.sha256"], cwd=self.usb, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_no_secret_on_the_usb(self):
        for p in self.usb.rglob("*"):
            if p.is_file():
                self.assertNotIn(b"SECRET-KEY", p.read_bytes(), p)

    def test_image_is_pulled_once_for_two_nodes(self):
        self.assertEqual(self.stub_log().count("docker pull nginx:1.27-alpine"), 1)

    def test_install_without_a_node_lists_them(self):
        r = self.run_script(self.usb / "scripts/install-flatcar.sh", root=self.tmp, check=False)
        self.assertEqual(r.returncode, 1)
        self.assertIn("node-a", r.stderr)
        self.assertIn("node-b", r.stderr)

    def test_install_as_non_root_stops_before_the_disk(self):
        import os
        if os.geteuid() == 0:
            self.skipTest("runs as root")
        r = self.run_script(self.usb / "scripts/install-flatcar.sh", "node-a", root=self.tmp, check=False)
        self.assertEqual(r.returncode, 1)
        self.assertIn("must run as root", r.stderr)
        self.assertNotIn("flatcar-install", self.stub_log())
