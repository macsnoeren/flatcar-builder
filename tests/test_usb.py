"""Phase 2: the USB and install-flatcar.sh."""

import json

from .helpers import Scratch, load_yaml, node


class UsbWithSecrets(Scratch):
    def setUp(self):
        super().setUp()
        n = node(secrets="secrets")
        n["apps"][0]["secrets"] = "apps/web/secrets"
        self.make(nodes={"node-a": n, "node-b": node()},
                  files={"nodes/node-a/secrets/etc/a/key": "SECRET-NODE\n",
                         "nodes/node-a/apps/web/secrets/etc/web/key": "SECRET-APP\n",
                         "nodes/node-a/secrets/etc/top": "SECRET-TOP\n"})
        self.assertBuilds("--mode", "usb", "--with-secrets")
        self.usb = self.dep / "build" / "usb"

    def test_secrets_sit_next_to_the_ignition_of_their_node(self):
        s = self.usb / "nodes/node-a/secrets"
        self.assertEqual((s / "etc/a/key").read_text(), "SECRET-NODE\n")
        self.assertEqual((s / "etc/web/key").read_text(), "SECRET-APP\n")
        self.assertEqual((s / "etc/a/key").stat().st_mode & 0o777, 0o600)
        self.assertFalse((self.usb / "nodes/node-b/secrets").exists())

    def test_secrets_stay_out_of_the_ignition_and_the_readme(self):
        for rel in ("nodes/node-a/config.bu", "nodes/node-a/config.ign", "README.txt"):
            self.assertNotIn("SECRET", (self.usb / rel).read_text(), rel)

    def test_checksums_cover_the_secrets(self):
        self.assertIn("nodes/node-a/secrets/etc/a/key", (self.usb / "checksums.sha256").read_text())

    def test_no_reminder_with_the_option(self):
        r = self.build("--mode", "usb", "--with-secrets", FLATCAR_BUILDER_QUIET="0")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("still needs an app bundle", r.stderr)

    def test_reminder_names_only_the_nodes_with_secrets(self):
        r = self.build("--mode", "usb", FLATCAR_BUILDER_QUIET="0")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("node node-a: its secrets are not on this USB, so it still needs an app bundle", r.stderr)
        self.assertNotIn("node node-b: its secrets", r.stderr)
        self.assertFalse((self.usb / "nodes/node-a/secrets").exists())

    def test_no_reminder_from_a_check(self):
        r = self.build("--check", FLATCAR_BUILDER_QUIET="0")
        self.assertNotIn("still needs an app bundle", r.stderr)

    def test_install_copies_them_onto_the_new_root(self):
        import os, shutil, stat, subprocess
        if not shutil.which("unshare") or subprocess.run(["unshare", "-r", "true"], capture_output=True).returncode:
            self.skipTest("needs user namespaces")
        stubs = self.tmp / "install-stubs"
        stubs.mkdir()
        for name in ("mount", "umount", "rmdir"):     # the new root is what this test reads afterwards
            (stubs / name).write_text("#!/bin/sh\nexit 0\n")
            (stubs / name).chmod(0o755)
        (self.usb / "flatcar/flatcar-install").write_text("#!/bin/sh\nexit 0\n")
        tmp = self.tmp / "mnt-parent"
        tmp.mkdir()
        env = dict(os.environ, PATH=f"{stubs}:{os.environ['PATH']}", TMPDIR=str(tmp))
        r = subprocess.run(["unshare", "-r", "bash", str(self.usb / "scripts/install-flatcar.sh"), "node-a", "/dev/x"],
                           env=env, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertNotIn("SECRET", r.stdout + r.stderr)
        new_root = next(tmp.iterdir())
        mode = lambda rel: stat.S_IMODE((new_root / rel).stat().st_mode)
        self.assertEqual((new_root / "etc/a/key").read_text(), "SECRET-NODE\n")
        self.assertEqual(mode("etc/a/key"), 0o600)
        self.assertEqual(mode("etc/a"), 0o700)
        self.assertEqual(mode("etc/web"), 0o700)
        self.assertEqual(mode("etc/top"), 0o600)
        self.assertEqual(mode("etc"), 0o755)


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
