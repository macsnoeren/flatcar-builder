"""Phase 2: platform files, overlays and directories."""

import json
import shutil
import subprocess
import unittest

from builder import NODE_DIR, config, rootfs
from builder.errors import ConfigError

from .helpers import Scratch, node

UNITS = NODE_DIR / "rootfs/etc/systemd/system"


class PlatformFiles(unittest.TestCase):
    @unittest.skipUnless(shutil.which("systemd-analyze"), "systemd-analyze not available")
    def test_units_verify(self):
        r = subprocess.run(["systemd-analyze", "verify", *map(str, sorted(UNITS.glob("*.service")))],
                           capture_output=True, text=True)
        # The node's own binaries do not exist on the build machine; anything else is a real problem.
        problems = [l for l in r.stderr.splitlines()
                    if l.strip() and "/opt/bin/" not in l]
        self.assertEqual(problems, [])

    def test_compose_unit_shape(self):
        u = (UNITS / "compose-stack.service").read_text()
        for want in ("Type=oneshot", "RemainAfterExit=yes", "Restart=on-failure", "up -d --remove-orphans",
                     "compose.yml stop", "EnvironmentFile=/etc/flatcar-builder/node.env"):
            self.assertIn(want, u)
        self.assertNotIn("Restart=always", u)
        self.assertNotIn(" down", u)
        self.assertNotIn("ExecStartPre", u)

    def test_watcher_unit_logs_to_its_own_file(self):
        u = (UNITS / "watch-install.service").read_text()
        self.assertIn("LOG_FILE=/var/log/watch-install.log", u)
        self.assertNotIn("LOG_HOST", u)


class LogCaps(Scratch):
    def test_defaults(self):
        self.make()
        dep = config.load_deployment(self.dep)
        d = json.loads(rootfs.daemon_json(dep))
        self.assertEqual(d["log-opts"], {"max-size": "10m", "max-file": "3"})
        self.assertEqual(rootfs.journald_conf(dep), "[Journal]\nSystemMaxUse=200M\nSystemMaxFileSize=20M\n")

    def test_overrides(self):
        self.make(deployment={"logs": {"docker": {"max_size": "50m", "max_file": 5},
                                       "journal": {"max_use": "1G", "max_file_size": "64M"}}})
        dep = config.load_deployment(self.dep)
        self.assertEqual(json.loads(rootfs.daemon_json(dep))["log-opts"], {"max-size": "50m", "max-file": "5"})
        self.assertIn("SystemMaxUse=1G\nSystemMaxFileSize=64M", rootfs.journald_conf(dep))


class NodeEnv(Scratch):
    def test_contents(self):
        self.make(nodes={"test-node": node(hostname="neo.example.org")})
        n = config.load_deployment(self.dep).nodes[0]
        self.assertEqual(rootfs.node_env(n), "NODE_NAME='test-node'\nHOSTNAME='neo.example.org'\n")

    def test_dollar_and_space_survive_bash(self):
        self.make()
        n = config.load_deployment(self.dep).nodes[0]
        n.name = "we $ird name"
        env = self.tmp / "node.env"
        env.write_text(rootfs.node_env(n))
        out = subprocess.run(["bash", "-c", f". {env}; printf %s \"$NODE_NAME\""], capture_output=True, text=True)
        self.assertEqual(out.stdout, "we $ird name")

    def test_quote_is_refused(self):
        self.make()
        n = config.load_deployment(self.dep).nodes[0]
        n.hostname = "it's"
        with self.assertRaises(ConfigError):
            rootfs.node_env(n)


class Overlays(Scratch):
    def tree(self, **kw):
        self.make(**kw)
        dep = config.load_deployment(self.dep)
        return rootfs.full_tree(dep, dep.nodes[0], "test"), dep

    def test_layers_meet(self):
        n = node(files="files")
        n["apps"][0]["files"] = "apps/web/files"
        t, _ = self.tree(nodes={"test-node": n}, deployment={"common_files": "common"},
                         files={"common/etc/common.conf": "c\n",
                                "nodes/test-node/files/etc/node.conf": "n\n",
                                "nodes/test-node/apps/web/files/etc/web/site.conf": "w\n"})
        for p in ("/etc/common.conf", "/etc/node.conf", "/etc/web/site.conf", "/etc/hostname",
                  "/etc/flatcar-builder/node.env", "/etc/systemd/network/10-eth0.network",
                  "/opt/compose/compose.yml", "/opt/compose/compose.d/10-web.yml",
                  "/var/lib/flatcar-builder/apps/web.json", "/opt/bin/watch-install.sh",
                  "/etc/systemd/system/compose-stack.service", "/etc/flatcar/update.conf"):
            self.assertIn(p, t.entries, p)

    def test_same_path_in_two_layers(self):
        with self.assertRaises(ConfigError) as cm:
            self.tree(nodes={"test-node": node(files="files")}, deployment={"common_files": "common"},
                      files={"common/etc/x.conf": "c\n", "nodes/test-node/files/etc/x.conf": "n\n"})
        self.assertIn("shipped twice", str(cm.exception))
        self.assertIn("common", str(cm.exception))
        self.assertIn("node", str(cm.exception))

    def test_node_may_override_daemon_json(self):
        t, _ = self.tree(nodes={"test-node": node(files="files")},
                         files={"nodes/test-node/files/etc/docker/daemon.json": '{"log-driver": "local"}\n'})
        self.assertEqual(t.entries["/etc/docker/daemon.json"].layer, "node")

    def test_node_may_not_override_a_unit(self):
        with self.assertRaises(ConfigError):
            self.tree(nodes={"test-node": node(files="files")},
                      files={"nodes/test-node/files/etc/systemd/system/compose-stack.service": "x\n"})

    def test_file_and_secret_on_one_path(self):
        self.make(nodes={"test-node": node(files="files", secrets="secrets")},
                  files={"nodes/test-node/files/etc/a/key": "public\n", "nodes/test-node/secrets/etc/a/key": "s\n"})
        dep = config.load_deployment(self.dep)
        with self.assertRaises(ConfigError) as cm:
            rootfs.validate(dep, dep.nodes[0])
        self.assertIn("both a file", str(cm.exception))

    def test_readme_and_dotfiles_stay_behind(self):
        t, _ = self.tree(nodes={"test-node": node(files="files")},
                         files={"nodes/test-node/files/etc/a/README.md": "x\n",
                                "nodes/test-node/files/etc/a/.gitignore": "*\n",
                                "nodes/test-node/files/etc/a/real.conf": "x\n"})
        self.assertIn("/etc/a/real.conf", t.entries)
        self.assertNotIn("/etc/a/README.md", t.entries)
        self.assertNotIn("/etc/a/.gitignore", t.entries)

    def test_executable_bit_is_kept(self):
        t, _ = self.tree()
        self.assertEqual(t.entries["/opt/bin/watch-install.sh"].mode, 0o755)
        self.assertEqual(t.entries["/etc/hostname"].mode, 0o644)

    def test_secrets_are_0600(self):
        self.make(nodes={"test-node": node(secrets="secrets")}, files={"nodes/test-node/secrets/etc/a/k": "s\n"})
        s = rootfs.secrets_tree(config.load_deployment(self.dep).nodes[0])
        self.assertEqual(s.entries["/etc/a/k"].mode, 0o600)


class Directories(Scratch):
    def dirs(self, node_dirs, app_dirs):
        n = node(directories=node_dirs)
        n["apps"][0]["directories"] = app_dirs
        self.make(nodes={"test-node": n})
        return rootfs.directories(config.load_deployment(self.dep).nodes[0])

    def test_same_path_same_mode_is_one(self):
        d = self.dirs([{"path": "/var/x", "mode": "0700"}], [{"path": "/var/x", "mode": "0700"}])
        self.assertEqual([p for p, _ in d].count("/var/x"), 1)
        self.assertIn(("/opt/deploy", 0o755), d)

    def test_same_path_other_mode(self):
        with self.assertRaises(ConfigError) as cm:
            self.dirs([{"path": "/var/x", "mode": "0700"}], [{"path": "/var/x", "mode": "0755"}])
        self.assertIn("/var/x", str(cm.exception))
