"""Phase 1: reading deployment.yml and node.yml."""

from pathlib import Path

from builder import config
from builder.errors import BuildError, ConfigError
from builder.version import satisfies

from .helpers import Scratch, node


class DeploymentFile(Scratch):
    def load(self):
        return config.load_deployment(self.dep)

    def test_valid_file_is_filled_in(self):
        self.make()
        d = self.load()
        self.assertEqual(d.channel, "stable")
        self.assertEqual(d.arch, "amd64-usr")
        self.assertEqual(d.docker_log_max_size, "10m")
        self.assertEqual(d.docker_log_max_file, 3)
        self.assertEqual(d.journal_max_use, "200M")
        self.assertEqual(d.journal_max_file_size, "20M")
        self.assertEqual(d.os_boot_check_timeout, "10m")
        self.assertEqual(d.ssh_keys, ["ssh-ed25519 AAAATESTKEY test@fixture"])
        self.assertEqual([n.name for n in d.nodes], ["test-node"])

    def assertBad(self, deployment, where, fragment=""):
        self.make(deployment=deployment)
        with self.assertRaises(ConfigError) as cm:
            self.load()
        self.assertIn(where, str(cm.exception))
        self.assertIn(fragment, str(cm.exception))

    def test_errors_name_file_and_field(self):
        self.assertBad({"flatcar": {"channel": "stable", "version": "4.x"}}, "deployment.yml: flatcar.version")
        self.assertBad({"flatcar": {"channel": "nightly", "version": "current"}}, "deployment.yml: flatcar.channel")
        self.assertBad({"compose_version": None}, "deployment.yml: compose_version", "is required")
        self.assertBad({"logs": {"docker": {"max_file": "three"}}}, "deployment.yml: logs.docker.max_file")

    def test_unknown_key_is_refused(self):
        self.assertBad({"flatcar_version": "1.2.3"}, "deployment.yml", "unknown key(s) flatcar_version")

    def test_plaintext_password_is_refused(self):
        self.assertBad({"access": {"ssh_authorized_keys_files": ["keys/test.pub"], "core_password_hash": "secret"}},
                       "access.core_password_hash", "crypt hash")

    def test_missing_ssh_key_file(self):
        self.assertBad({"access": {"ssh_authorized_keys_files": ["keys/nope.pub"]}},
                       "access.ssh_authorized_keys_files[0]", "not found")

    def test_missing_deployment_yml(self):
        self.dep.mkdir()
        with self.assertRaises(ConfigError) as cm:
            self.load()
        self.assertIn("has no deployment.yml", str(cm.exception))


class Paths(Scratch):
    def test_path_in_deployment_yml_is_relative_to_it(self):
        self.make(deployment={"common_files": "common/files"}, files={"common/files/etc/x": "x\n"})
        self.assertEqual(config.load_deployment(self.dep).common_files, (self.dep / "common/files").resolve())

    def test_path_in_node_yml_is_relative_to_it(self):
        n = node()
        n["apps"][0]["files"] = "../../shared/web"
        self.make(nodes={"test-node": n}, files={"shared/web/etc/x": "x\n"})
        app = config.load_deployment(self.dep).nodes[0].apps[0]
        self.assertEqual(app.files, (self.dep / "shared/web").resolve())

    def test_absolute_path_stays_absolute(self):
        n = node()
        n["apps"][0]["files"] = str(self.tmp / "abs")
        (self.tmp / "abs").mkdir()
        self.make(nodes={"test-node": n})
        self.assertEqual(config.load_deployment(self.dep).nodes[0].apps[0].files, self.tmp / "abs")


class NodeFile(Scratch):
    def load_node(self, **changes):
        self.make(nodes={"test-node": node(**changes)})
        return config.load_deployment(self.dep).nodes[0]

    def test_defaults(self):
        n = self.load_node()
        self.assertEqual(n.hostname, "test-node")
        self.assertEqual(n.project, "test-node")
        self.assertIsNone(n.files)
        self.assertIsNone(n.secrets)

    def test_project_name_is_made_compose_safe(self):
        self.make(nodes={"Neo.Node": node()})
        self.assertEqual(config.load_deployment(self.dep).nodes[0].project, "neo-node")

    def test_files_must_be_a_directory(self):
        with self.assertRaises(ConfigError) as cm:
            self.load_node(files="nowhere")
        self.assertIn("node.yml: files", str(cm.exception))
        self.assertIn("not a directory", str(cm.exception))

    def test_secrets_must_be_a_directory(self):
        with self.assertRaises(ConfigError):
            self.load_node(secrets="nowhere")

    def test_mode_must_be_an_octal_string(self):
        for bad in (755, "755x", "0999", 493):
            with self.assertRaises(ConfigError, msg=repr(bad)) as cm:
                self.load_node(directories=[{"path": "/var/x", "mode": bad}])
            self.assertIn("directories[0].mode", str(cm.exception))
        n = self.load_node(directories=[{"path": "/var/x", "mode": "0700"}])
        self.assertEqual(n.directories[0].mode, 0o700)

    def test_directory_path_must_be_absolute(self):
        with self.assertRaises(ConfigError):
            self.load_node(directories=[{"path": "var/x", "mode": "0755"}])


class NodeSelection(Scratch):
    def test_default_is_every_directory_in_nodes(self):
        self.make(nodes={"b": node(), "a": node()})
        self.assertEqual([n.name for n in config.load_deployment(self.dep).nodes], ["a", "b"])

    def test_nodes_list_wins(self):
        self.make(nodes={"b": node(), "a": node()}, deployment={"nodes": ["b"]})
        self.assertEqual([n.name for n in config.load_deployment(self.dep).nodes], ["b"])

    def test_node_flag_limits_the_build(self):
        self.make(nodes={"b": node(), "a": node()})
        r = self.assertBuilds("--check", "--node", "b")
        self.assertIn("Validating 1 node(s): b", r.stderr)

    def test_unknown_node_flag_names_the_known_ones(self):
        self.make(nodes={"b": node(), "a": node()})
        self.assertRefused("the deployment has: a, b", "--check", "--node", "c")

    def test_node_directory_without_node_yml(self):
        self.make()
        (self.dep / "nodes" / "empty").mkdir()
        with self.assertRaises(ConfigError) as cm:
            config.load_deployment(self.dep)
        self.assertIn("has no node.yml", str(cm.exception))


class Apps(Scratch):
    def assertBadApp(self, fragment, mutate, parts=None):
        n = node()
        mutate(n["apps"])
        self.make(nodes={"test-node": n}, parts=parts)
        with self.assertRaises(ConfigError) as cm:
            config.load_deployment(self.dep)
        self.assertIn(fragment, str(cm.exception))

    def test_duplicate_app_name(self):
        self.assertBadApp("defined twice", lambda apps: apps.append(dict(apps[0])))

    def test_build_and_pull(self):
        def m(apps):
            apps[0]["images"][0]["build"] = {"context": "."}
        self.assertBadApp("both 'build' and 'pull: true'", m)

    def test_neither_build_nor_pull(self):
        def m(apps):
            apps[0]["images"][0].pop("pull")
        self.assertBadApp("needs either 'build' or 'pull: true'", m)

    def test_compose_file_must_exist(self):
        def m(apps):
            apps[0]["compose"] = "compose.d/99-none.yml"
        self.assertBadApp("99-none.yml not found", m)

    def test_build_arg_must_be_a_string(self):
        def m(apps):
            apps[0]["images"] = [{"image": "x:1", "build": {"context": "ctx", "args": {"A": ["no"]}}}]
        self.write("nodes/test-node/ctx/Dockerfile", "FROM scratch\n")
        self.assertBadApp("images[0].build.args.A", m)

    def test_build_context_without_dockerfile(self):
        def m(apps):
            apps[0]["images"] = [{"image": "x:1", "build": {"context": "ctx"}}]
        self.write("nodes/test-node/ctx/main.go", "package main\n")
        self.assertBadApp("has no Dockerfile", m)

    def test_empty_context_points_at_the_submodule(self):
        def m(apps):
            apps[0]["images"] = [{"image": "x:1", "build": {"context": "ctx"}}]
        (self.dep / "nodes/test-node/ctx").mkdir(parents=True)
        self.assertBadApp("git submodule update --init --recursive", m)

    def test_replace_dir_too_close_to_root(self):
        def m(apps):
            apps[0]["replace_dirs"] = ["/etc"]
        self.assertBadApp("too close to /", m)

    def test_full_app_is_read(self):
        n = node()
        n["apps"][0].update({
            "images": [{"image": "x:1", "build": {"context": "ctx", "dockerfile": "ctx/web.Dockerfile",
                                                  "args": {"B": "2", "A": "1"}}}],
            "directories": [{"path": "/etc/web", "mode": "0700"}],
            "replace_dirs": ["/etc/web/conf.d"],
            "hooks": {"post_update": "hooks/test.sh"},
        })
        self.make(nodes={"test-node": n}, files={"nodes/test-node/ctx/web.Dockerfile": "FROM scratch\n",
                                                  "nodes/test-node/hooks/test.sh": "true\n"})
        app = config.load_deployment(self.dep).nodes[0].apps[0]
        self.assertEqual(list(app.images[0].args.items()), [("B", "2"), ("A", "1")])
        self.assertEqual(app.directories[0].mode, 0o700)
        self.assertEqual(app.replace_dirs, ["/etc/web/conf.d"])
        self.assertTrue(app.post_update.name, "test.sh")


class BuilderVersion(Scratch):
    def test_ranges(self):
        self.assertTrue(satisfies("1.2.0", ">=1.0,<2"))
        self.assertFalse(satisfies("2.0.0", ">=1.0,<2"))
        self.assertFalse(satisfies("0.9.9", ">=1.0"))
        self.assertTrue(satisfies("0.1.0", "==0.1"))
        with self.assertRaises(ConfigError):
            satisfies("1.0.0", "~1.0")

    def test_a_range_that_does_not_fit_stops_the_build(self):
        self.make(deployment={"builder": ">=5"})
        r = self.assertRefused("needs Flatcar Builder >=5", "--check")
        self.assertIn("this is 0.1.0", r.stderr)

    def test_no_range_is_a_warning(self):
        self.make(deployment={"builder": None})
        r = self.assertBuilds("--check")
        self.assertIn("has no 'builder:' range", r.stderr)
