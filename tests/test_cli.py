"""Phase 1: the entrypoint, the arguments and --check."""

import os
import subprocess

from .helpers import Scratch, TOOL, node


class EntryPoint(Scratch):
    def test_help_shows_usage(self):
        r = subprocess.run(["bash", str(TOOL / "build-flatcar.sh"), "--help"], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0)
        self.assertIn("--mode", r.stdout)
        self.assertIn("deployment", r.stdout)

    def test_missing_pyyaml_is_explained(self):
        # A module called yaml that fails to import, first on PYTHONPATH.
        fake = self.tmp / "noyaml"
        fake.mkdir()
        (fake / "yaml.py").write_text("raise ImportError('simulated')\n")
        env = dict(os.environ, PYTHONPATH=str(fake))
        r = subprocess.run(["bash", str(TOOL / "build-flatcar.sh"), "--help"], env=env,
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 2)
        self.assertIn("PyYAML not found", r.stderr)
        self.assertIn("apt install python3-yaml", r.stderr)


class Arguments(Scratch):
    def test_default_mode_is_usb(self):
        from builder.cli import parse_args
        self.assertEqual(parse_args(["d"]).mode, "usb")

    def test_unknown_mode_is_refused(self):
        self.make()
        r = self.build("--mode", "magic")
        self.assertEqual(r.returncode, 2)
        self.assertIn("invalid choice", r.stderr)

    def test_app_needs_mode_app(self):
        self.make()
        for mode in ("usb", "os"):
            r = self.build("--mode", mode, "--app", "web")
            self.assertEqual(r.returncode, 2, mode)
            self.assertIn("--app only works with --mode app", r.stderr)

    def test_missing_deployment_dir_names_the_path(self):
        r = self.build("--check")
        self.assertEqual(r.returncode, 1)
        self.assertIn(str(self.dep), r.stderr)
        self.assertIn("not found", r.stderr)


class Check(Scratch):
    def test_check_uses_neither_docker_nor_network(self):
        self.make()
        r = self.build("--check", STUB_DOCKER_FAIL=1, http_proxy="http://127.0.0.1:9", https_proxy="http://127.0.0.1:9")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("Check passed", r.stderr)
        self.assertFalse((self.dep / "build").exists())
        # The only docker call --check makes is asking whether the compose plugin exists.
        for line in self.stub_log().splitlines():
            self.assertEqual(line, "docker compose version")

    def test_check_fails_on_an_invalid_deployment(self):
        self.make(nodes={"test-node": node(install_device="sda")})
        r = self.build("--check", STUB_DOCKER_FAIL=1)
        self.assertEqual(r.returncode, 1)

    def test_minimal_example_passes(self):
        r = subprocess.run(["bash", str(TOOL / "build-flatcar.sh"), str(TOOL / "examples" / "minimal"), "--check"],
                           env=self.env(), capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
