"""Phases 3–5: the scripts that run ON the node, against a scratch root.

Every node script takes ROOT as a prefix for the paths it touches, and calls
docker, docker-compose and systemctl through PATH, so here they run as an
ordinary user with the stand-ins in tests/stubs/.
"""

import os
import stat
import subprocess
import tarfile
import time

from builder import NODE_DIR

from .helpers import STUBS, Scratch
from .test_bundle import Base

WATCH = NODE_DIR / "rootfs/opt/bin/watch-install.sh"
HEALTH = NODE_DIR / "rootfs/opt/bin/health-check.sh"


class NodeBase(Base):
    def setUp(self):
        super().setUp()
        self.stack()
        self.root = self.tmp / "root"
        (self.root / "etc/flatcar-builder").mkdir(parents=True)
        (self.root / "etc/flatcar-builder/node.env").write_text("NODE_NAME='test-node'\nHOSTNAME='test-node'\n")

    def bundle(self, *args):
        before = set(self.bundles())
        self.assertBuilds(*args)
        tar = (set(self.bundles()) - before).pop()
        return self.extract(tar)

    def install(self, bundle, *args, check=True, **env):
        self.log.write_text("")
        return self.run_script(bundle / "install.sh", *args, root=self.root, check=check, **env)


class FullApp(NodeBase):
    def setUp(self):
        super().setUp()
        # What an earlier stack left behind.
        (self.root / "etc/api/conf.d").mkdir(parents=True)
        (self.root / "etc/api/conf.d/old.conf").write_text("stale\n")
        (self.root / "etc/api/notes.txt").write_text("keep\n")
        (self.root / "etc/db/password").mkdir(parents=True)              # a bind mount made this a directory
        (self.root / "opt/images").mkdir(parents=True)
        (self.root / "opt/images/gone_1.tar").write_text("old\n")
        self.x = self.bundle("--mode", "app")
        self.r = self.install(self.x)

    def test_files_land(self):
        self.assertEqual((self.root / "opt/compose/compose.d/20-db.yml").read_text(),
                         (self.dep / "nodes/test-node/compose.d/20-db.yml").read_text())
        self.assertTrue((self.root / "opt/compose/compose.yml").is_file())
        self.assertTrue((self.root / "etc/systemd/system/compose-stack.service").is_file())
        self.assertTrue(os.access(self.root / "opt/bin/watch-install.sh", os.X_OK))

    def test_replace_dir_is_emptied_first(self):
        self.assertFalse((self.root / "etc/api/conf.d/old.conf").exists())
        self.assertTrue((self.root / "etc/api/conf.d/a.conf").is_file())

    def test_other_files_stay(self):
        self.assertEqual((self.root / "etc/api/notes.txt").read_text(), "keep\n")

    def test_directories_get_their_mode(self):
        self.assertEqual(stat.S_IMODE((self.root / "var/lib/db").stat().st_mode), 0o700)
        self.assertTrue((self.root / "opt/deploy").is_dir())

    def test_secret_replaces_the_bind_mount_directory(self):
        p = self.root / "etc/db/password"
        self.assertTrue(p.is_file())
        self.assertEqual(stat.S_IMODE(p.stat().st_mode), 0o600)
        self.assertIn("etc/db/password", self.r.stdout)
        self.assertNotIn("SECRET-DB", self.r.stdout)

    def test_unused_image_tar_goes(self):
        self.assertFalse((self.root / "opt/images/gone_1.tar").exists())
        self.assertTrue((self.root / "opt/images/postgres_15.tar").exists())

    def test_order_on_the_node(self):
        log = self.stub_log().splitlines()
        load = next(i for i, l in enumerate(log) if l.startswith("docker load"))
        reload_ = log.index("systemctl daemon-reload")
        up = next(i for i, l in enumerate(log) if l.startswith("docker-compose") and " up -d " in l)
        self.assertLess(load, up)
        self.assertLess(reload_, up)
        self.assertIn("systemctl enable compose-stack.service", log)
        self.assertTrue(log[up].endswith("up -d --remove-orphans"), log[up])

    def test_no_reboot(self):
        self.assertNotIn("systemctl reboot", self.stub_log())

    def test_hook_ran(self):
        self.assertIn("hook ran", self.r.stdout)

    def test_app_records(self):
        import json
        rec = json.loads((self.root / "var/lib/flatcar-builder/apps/db.json").read_text())
        self.assertEqual(rec["images"], ["postgres:15"])
        self.assertTrue(rec["bundle"].startswith("test-node-app-"))
        self.assertTrue((self.root / "var/lib/flatcar-builder/last-bundle.json").is_file())

    def test_log_file(self):
        log = (self.root / "var/log/install.log").read_text()
        self.assertIn("[install] bundle test-node-app-", log)
        self.assertIn("[install] done", log)

    def test_auto_updates_off(self):
        self.assertIn("SERVER=disabled", (self.root / "etc/flatcar/update.conf").read_text())


class Refusals(NodeBase):
    def test_failing_hook_is_a_warning(self):
        self.write("nodes/test-node/hooks/api.sh", "echo failing; exit 3\n")
        x = self.bundle("--mode", "app")
        r = self.install(x)
        self.assertIn("the post-update hook of api failed", r.stdout)

    def test_dry_run_changes_nothing(self):
        x = self.bundle("--mode", "app")
        r = self.install(x, "--dry-run")
        self.assertIn("DRY RUN", r.stdout)
        self.assertFalse((self.root / "opt/compose").exists())
        self.assertEqual(self.stub_log(), "")

    def test_bundle_for_another_node(self):
        (self.root / "etc/flatcar-builder/node.env").write_text("NODE_NAME='other'\n")
        x = self.bundle("--mode", "app")
        r = self.install(x, check=False)
        self.assertEqual(r.returncode, 1)
        self.assertIn("this bundle is for node test-node, this is other", r.stdout)
        self.assertFalse((self.root / "opt/compose").exists())

    def test_checksum_mismatch(self):
        x = self.bundle("--mode", "app")
        (x / "rootfs/opt/compose/compose.yml").write_text("tampered\n")
        r = self.install(x, check=False)
        self.assertEqual(r.returncode, 1)
        self.assertIn("does not match its checksums", r.stdout)


class Partial(NodeBase):
    def test_app_not_installed_is_refused(self):
        x = self.bundle("--mode", "app", "--app", "db")
        r = self.install(x, check=False)
        self.assertEqual(r.returncode, 1)
        self.assertIn("app db is not installed on this node", r.stdout)
        self.assertFalse((self.root / "opt/compose").exists())

    def test_update_of_one_app(self):
        self.install(self.bundle("--mode", "app"))
        web_before = (self.root / "opt/compose/compose.d/10-web.yml").read_text()
        self.write("nodes/test-node/compose.d/20-db.yml", DB_CHANGED)
        time.sleep(0.01)
        x = self.bundle("--mode", "app", "--app", "db")
        r = self.install(x)
        log = self.stub_log()
        self.assertIn("up -d --no-deps db db-init", log)
        self.assertNotIn("--remove-orphans", log)
        self.assertEqual((self.root / "opt/compose/compose.d/20-db.yml").read_text(), DB_CHANGED)
        self.assertEqual((self.root / "opt/compose/compose.d/10-web.yml").read_text(), web_before)
        self.assertTrue((self.root / "opt/compose/compose.d/30-api.yml").exists())
        self.assertTrue((self.root / "var/lib/flatcar-builder/apps/web.json").exists())
        self.assertNotIn("systemctl reboot", log)
        self.assertNotIn("systemctl enable", log)          # no platform units in a bundle of apps


DB_CHANGED = "services:\n  db:\n    image: postgres:15\n    command: [postgres, -c, max_connections=50]\n  db-init:\n    image: postgres:15\n"


class OsUpdate(NodeBase):
    def os_release(self, version):
        (self.root / "etc").mkdir(exist_ok=True)
        (self.root / "etc/os-release").write_text(f'NAME="Flatcar Container Linux by Kinvolk"\nVERSION_ID={version}\n')

    def test_other_version_is_written_and_rebooted(self):
        self.os_release("1.0.0")
        x = self.bundle("--mode", "os")
        self.install(x)
        log = self.stub_log()
        self.assertIn(f"flatcar-update --to-version 9999.0.0 --to-payload {x}/updates/flatcar_production_update.gz", log)
        self.assertIn("systemctl mask update-engine", log)
        self.assertTrue(log.rstrip().endswith("systemctl reboot"))
        self.assertNotIn(" up -d", log)         # the reboot brings the stack up

    def test_same_version_is_skipped(self):
        self.os_release("9999.0.0")
        x = self.bundle("--mode", "os")
        r = self.install(x)
        log = self.stub_log()
        self.assertNotIn("flatcar-update --to-version", log)
        self.assertNotIn("systemctl reboot", log)
        self.assertIn("already at 9999.0.0", r.stdout)
        self.assertIn(" up -d --remove-orphans", log)

    def test_failed_os_update_does_not_reboot(self):
        self.os_release("1.0.0")
        x = self.bundle("--mode", "os")
        r = self.install(x, check=False, STUB_FLATCAR_UPDATE_RC=1)
        self.assertEqual(r.returncode, 1)
        self.assertNotIn("systemctl reboot", self.stub_log())


class Watcher(Scratch):
    def setUp(self):
        super().setUp()
        self.deploy = self.tmp / "deploy"
        self.deploy.mkdir()
        self.watch_log = self.tmp / "watch-install.log"

    def tar(self, name, script):
        src = self.tmp / f"src-{name}"
        src.mkdir()
        if script is not None:
            (src / "install.sh").write_text(script)
        with tarfile.open(self.deploy / f"{name}.tar", "w") as t:
            t.add(src, arcname=".")

    def watch(self):
        return subprocess.run(["bash", str(WATCH)], env=self.env(
            DEPLOY_DIR=self.deploy, DELAY=0, POLL_INTERVAL=0, WATCH_ONCE=1, LOG_FILE=self.watch_log, NODE_NAME="n"),
            capture_output=True, text=True, timeout=30)

    def test_approved_bundle_runs_once(self):
        ran = self.tmp / "ran"
        marker = self.deploy / "b1.tar.installed"
        self.tar("b1", f'[ -e "{marker}" ] && echo marker-was-there >> "{ran}"; echo run >> "{ran}"\n')
        (self.deploy / "b1.tar.approved").touch()
        self.watch()
        self.watch()
        self.assertEqual(ran.read_text(), "marker-was-there\nrun\n")
        self.assertIn("installed", marker.read_text())
        self.assertIn("[watch-install] installation of 'b1.tar' SUCCEEDED", self.watch_log.read_text())

    def test_unapproved_bundle_waits(self):
        self.tar("b2", "exit 0\n")
        self.watch()
        self.assertFalse((self.deploy / "b2.tar.installed").exists())

    def test_bundle_without_install_sh(self):
        self.tar("b3", None)
        (self.deploy / "b3.tar.approved").touch()
        self.watch()
        self.assertIn("FAILED: no install.sh", (self.deploy / "b3.tar.installed").read_text())

    def test_failing_install(self):
        self.tar("b4", "exit 7\n")
        (self.deploy / "b4.tar.approved").touch()
        self.watch()
        self.assertIn("FAILED: install.sh exited 7", (self.deploy / "b4.tar.installed").read_text())

    def test_pruning_keeps_three(self):
        work = self.deploy / "work"
        for i in range(5):
            d = work / f"old{i}"
            d.mkdir(parents=True)
            os.utime(d, (1000 + i, 1000 + i))
        self.watch()
        self.assertEqual(sorted(p.name for p in work.iterdir()), ["old2", "old3", "old4"])

    def test_no_network_logging(self):
        self.assertNotIn("nc ", WATCH.read_text())
        self.assertNotIn("/dev/udp", WATCH.read_text())


class Health(Scratch):
    def check(self, ps, active=0):
        root = self.tmp / "root"
        (root / "opt/compose").mkdir(parents=True, exist_ok=True)
        (root / "opt/compose/compose.yml").write_text("name: x\n")
        return subprocess.run(["bash", str(HEALTH)], env=self.env(
            ROOT=root, DOCKER_COMPOSE=self.tmp / "dc", STUB_PS=ps, STUB_ACTIVE=active),
            capture_output=True, text=True)

    def setUp(self):
        super().setUp()
        dc = self.tmp / "dc"
        dc.write_text((STUBS / "docker-compose").read_text())
        dc.chmod(0o755)

    def test_all_running(self):
        r = self.check("web\\trunning\\t0\\ninit\\texited\\t0\\n")
        self.assertEqual(r.returncode, 0, r.stdout)

    def test_exited_container_fails(self):
        r = self.check("web\\trunning\\t0\\ndb\\texited\\t1\\n")
        self.assertEqual(r.returncode, 1)
        self.assertIn("FAIL db exited with code 1", r.stdout)

    def test_restarting_container_fails(self):
        r = self.check("web\\trestarting\\t1\\n")
        self.assertIn("FAIL web (restarting)", r.stdout)

    def test_inactive_unit_fails(self):
        r = self.check("web\\trunning\\t0\\n", active=3)
        self.assertIn("FAIL compose-stack.service is not active", r.stdout)
