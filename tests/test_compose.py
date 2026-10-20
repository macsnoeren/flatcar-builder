"""Phase 2: the compose stack."""

from builder import compose, config
from builder.errors import BuildError, ConfigError

from .helpers import WEB_PART, Scratch, load_yaml, node


def two_apps():
    n = node()
    n["apps"].append({"name": "db", "compose": ["compose.d/20-db.yml", "compose.d/21-db-extra.yml"],
                      "images": [{"image": "postgres:15", "pull": True}]})
    return n


PARTS = {
    "compose.d/10-web.yml": WEB_PART,
    "compose.d/20-db.yml": "services:\n  db:\n    image: postgres:15\n",
    "compose.d/21-db-extra.yml": "services:\n  db-backup:\n    image: postgres:15\n",
}


class Root(Scratch):
    def test_root_lists_the_parts_in_app_order(self):
        self.make(nodes={"test-node": two_apps()}, parts={"test-node": PARTS})
        n = config.load_deployment(self.dep).nodes[0]
        import yaml
        root = yaml.safe_load(compose.root_yaml(n))
        self.assertEqual(root["name"], "test-node")
        self.assertEqual(root["include"], ["compose.d/10-web.yml", "compose.d/20-db.yml", "compose.d/21-db-extra.yml"])

    def test_parts_are_copied_byte_for_byte(self):
        self.make(nodes={"test-node": two_apps()}, parts={"test-node": PARTS})
        n = config.load_deployment(self.dep).nodes[0]
        compose.write_stack(n, self.tmp / "stack")
        for rel, text in PARTS.items():
            self.assertEqual((self.tmp / "stack" / rel).read_text(), text)

    def test_services_per_app(self):
        self.make(nodes={"test-node": two_apps()}, parts={"test-node": PARTS})
        n = config.load_deployment(self.dep).nodes[0]
        self.assertEqual(compose.app_services(n, n.app("db")), ["db", "db-backup"])

    def test_two_parts_with_one_name(self):
        n = two_apps()
        n["apps"][1]["compose"] = ["other/10-web.yml"]
        self.make(nodes={"test-node": n}, parts={"test-node": {**PARTS, "other/10-web.yml": "services: {}\n"}})
        with self.assertRaises(ConfigError) as cm:
            compose.check_part_names(config.load_deployment(self.dep).nodes[0])
        self.assertIn("10-web.yml", str(cm.exception))


class Validate(Scratch):
    def broken(self):
        self.make(nodes={"test-node": two_apps()},
                  parts={"test-node": {**PARTS, "compose.d/21-db-extra.yml": "services:\n  broken: [unclosed\n"}})

    def test_broken_part_without_plugin(self):
        self.broken()
        self.assertRefused("21-db-extra.yml", "--check")

    def test_broken_part_with_plugin(self):
        self.broken()
        r = self.assertRefused("21-db-extra.yml", "--check", STUB_COMPOSE=1)
        self.assertIn("docker compose", self.stub_log())

    def test_plugin_is_asked_to_check_the_merged_stack(self):
        self.make()
        self.assertBuilds("--check", STUB_COMPOSE=1)
        self.assertIn("docker compose -f compose.yml config --quiet", self.stub_log())

    def test_without_plugin_there_is_a_warning(self):
        self.make()
        r = self.assertBuilds("--check")
        self.assertIn("only the YAML syntax", r.stderr)


class Images(Scratch):
    def test_image_not_shipped_by_the_app(self):
        self.make(parts={"test-node": {"compose.d/10-web.yml": WEB_PART.replace("nginx:1.27-alpine", "never:1")}})
        self.assertRefused("the tar will not reach the node", "--check")

    def test_image_only_in_a_second_part(self):
        self.make(nodes={"test-node": two_apps()},
                  parts={"test-node": {**PARTS, "compose.d/21-db-extra.yml": "services:\n  x:\n    image: other:2\n"}})
        r = self.assertRefused("other:2", "--check")
        self.assertIn("app db", r.stderr)

    def test_unused_image_is_a_warning(self):
        n = node()
        n["apps"][0]["images"].append({"image": "busybox:1", "pull": True})
        self.make(nodes={"test-node": n})
        r = self.assertBuilds("--check")
        self.assertIn("busybox:1 is shipped but no service of the app uses it", r.stderr)

    def test_build_in_compose_is_refused(self):
        self.make(parts={"test-node": {"compose.d/10-web.yml": WEB_PART + "    build: .\n"}})
        self.assertRefused("'build:' is not allowed", "--check")
