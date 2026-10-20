"""Phases 3–5: bundles per node, of selected apps, and with the OS."""

import json
import re
import stat
import subprocess
import tarfile

from .helpers import WEB_PART, Scratch, node

DB_PART = "services:\n  db:\n    image: postgres:15\n  db-init:\n    image: postgres:15\n"
API_PART = "services:\n  api:\n    image: postgres:15\n"


def stack_node(**kw):
    n = node(**kw)
    n["apps"] += [
        {"name": "db", "compose": "compose.d/20-db.yml", "images": [{"image": "postgres:15", "pull": True}],
         "directories": [{"path": "/var/lib/db", "mode": "0700"}], "secrets": "apps/db/secrets"},
        {"name": "api", "compose": "compose.d/30-api.yml", "images": [{"image": "postgres:15", "pull": True}],
         "files": "apps/api/files", "hooks": {"post_update": "hooks/api.sh"}, "replace_dirs": ["/etc/api/conf.d"]},
    ]
    return n


PARTS = {"compose.d/10-web.yml": WEB_PART, "compose.d/20-db.yml": DB_PART, "compose.d/30-api.yml": API_PART}


class Base(Scratch):
    def stack(self, nodes=None):
        names = nodes or ["test-node"]
        files = {}
        for n in names:
            files[f"nodes/{n}/apps/db/secrets/etc/db/password"] = "SECRET-DB\n"
            files[f"nodes/{n}/apps/api/files/etc/api/conf.d/a.conf"] = "a\n"
            files[f"nodes/{n}/hooks/api.sh"] = "echo hook ran\n"
        self.make(nodes={n: stack_node() for n in names}, parts={n: PARTS for n in names}, files=files)

    def listing(self, tar):
        with tarfile.open(tar) as t:
            return {m.name: m for m in t.getmembers()}


class AppBundle(Base):
    def setUp(self):
        super().setUp()
        n2 = node()
        n2["apps"][0]["images"].append({"image": "only-b:1", "pull": True})
        self.stack()
        self.write("nodes/node-b/node.yml", __import__("yaml").safe_dump(n2))
        self.write("nodes/node-b/compose.d/10-web.yml", WEB_PART + "  b:\n    image: only-b:1\n")
        self.assertBuilds("--mode", "app")

    def test_one_tar_per_node(self):
        names = [p.name for p in self.bundles()]
        self.assertEqual(len(names), 2)
        self.assertTrue(re.fullmatch(r"node-b-app-\d{8}-\d{4}\.tar", names[0]), names)
        self.assertTrue(names[1].startswith("test-node-app-"))

    def test_install_sh_at_the_root(self):
        self.assertIn("./install.sh", self.listing(self.bundles()[1]))

    def test_only_the_images_of_the_node(self):
        test_node = self.listing(self.bundles()[1])
        self.assertNotIn("./images/only-b_1.tar", test_node)
        self.assertIn("./images/postgres_15.tar", test_node)
        self.assertIn("./images/only-b_1.tar", self.listing(self.bundles()[0]))

    def test_secret_is_0600_and_root_owned(self):
        m = self.listing(self.bundles()[1])["./secrets/etc/db/password"]
        self.assertEqual(stat.S_IMODE(m.mode), 0o600)
        self.assertEqual((m.uid, m.gid), (0, 0))

    def test_full_bundle_contents(self):
        x = self.extract(self.bundles()[1])
        for rel in ("MANIFEST.json", "bundle.env", "units.list", "bin/docker-compose", "lib/update-app.sh",
                    "rootfs/opt/compose/compose.yml", "rootfs/opt/compose/compose.d/30-api.yml",
                    "rootfs/etc/api/conf.d/a.conf", "rootfs/var/lib/flatcar-builder/apps/db.json",
                    "apps/api/hooks/post-update.sh", "apps/db/services", "checksums.sha256"):
            self.assertTrue((x / rel).is_file(), rel)
        self.assertFalse((x / "updates").exists())
        self.assertIn("/opt/compose/compose.d\n", (x / "replace_dirs.list").read_text())
        self.assertIn("/etc/api/conf.d\n", (x / "replace_dirs.list").read_text())
        self.assertIn("0700\t/var/lib/db\n", (x / "directories.list").read_text())
        self.assertEqual((x / "apps/db/services").read_text(), "db\ndb-init\n")
        r = subprocess.run(["sha256sum", "--quiet", "-c", "checksums.sha256"], cwd=x, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout)

    def test_manifest(self):
        x = self.extract(self.bundles()[1])
        m = json.loads((x / "MANIFEST.json").read_text())
        self.assertEqual(set(m), {"format", "bundle", "node", "hostname", "mode", "partial", "apps", "images",
                                  "flatcar", "built_at", "builder", "deployment"})
        self.assertEqual(m["node"], "test-node")
        self.assertEqual(m["mode"], "app")
        self.assertFalse(m["partial"])
        self.assertEqual([a["name"] for a in m["apps"]], ["web", "db", "api"])
        self.assertEqual(m["apps"][1]["services"], ["db", "db-init"])
        self.assertEqual(m["builder"]["version"], "0.1.0")
        # The scratch deployment is not a git checkout.
        self.assertEqual(m["deployment"], {"commit": None, "dirty": None})


class SelectedApps(Base):
    def test_missing_app_names_the_node(self):
        self.stack()
        self.assertRefused("node test-node has no app nope", "--mode", "app", "--app", "nope")

    def test_contents(self):
        self.stack()
        self.assertBuilds("--mode", "app", "--app", "db", "--app", "api")
        tar = self.bundles()[0]
        self.assertTrue(tar.name.startswith("test-node-app-db+api-"), tar.name)
        x = self.extract(tar)
        self.assertEqual((x / "images.list").read_text(), "postgres_15.tar\n")
        self.assertTrue((x / "rootfs/opt/compose/compose.d/20-db.yml").is_file())
        self.assertTrue((x / "rootfs/var/lib/flatcar-builder/apps/api.json").is_file())
        self.assertTrue((x / "secrets/etc/db/password").is_file())
        self.assertFalse((x / "rootfs/opt/compose/compose.yml").exists())
        self.assertFalse((x / "rootfs/opt/compose/compose.d/10-web.yml").exists())
        self.assertFalse((x / "rootfs/etc/hostname").exists())
        self.assertFalse((x / "bin").exists())
        self.assertFalse((x / "units.list").exists())
        self.assertEqual((x / "replace_dirs.list").read_text(), "/etc/api/conf.d\n")
        self.assertIn("PARTIAL='1'", (x / "bundle.env").read_text())
        self.assertIn("APPS='db api'", (x / "bundle.env").read_text())


class OsBundle(Base):
    def test_contents(self):
        self.stack()
        self.assertBuilds("--mode", "os")
        names = self.listing(self.bundles()[0])
        for rel in ("./updates/flatcar_production_update.gz", "./updates/TARGET_FLATCAR_VERSION",
                    "./lib/update-os.sh", "./images/postgres_15.tar", "./rootfs/opt/compose/compose.yml"):
            self.assertIn(rel, names)
        x = self.extract(self.bundles()[0])
        self.assertEqual((x / "updates/TARGET_FLATCAR_VERSION").read_text(), "9999.0.0\n")
        self.assertIn("TARGET_FLATCAR_VERSION='9999.0.0'", (x / "bundle.env").read_text())

    def test_no_payload_and_no_network(self):
        self.stack()
        (self.cache / "flatcar/amd64-usr/9999.0.0/flatcar_production_update.gz").unlink()
        r = self.assertRefused("could not download flatcar_production_update.gz", "--mode", "os",
                               http_proxy="http://127.0.0.1:9", https_proxy="http://127.0.0.1:9")
        self.assertIn("flatcar_production_update.gz", r.stderr)
        self.assertIn("update.release.flatcar-linux.net", r.stderr)
