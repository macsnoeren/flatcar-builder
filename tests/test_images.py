"""Phase 3: collecting, building, pulling and saving images."""

from builder import config, images
from builder.errors import BuildError

from .helpers import Scratch, node


def built(name, ctx="ctx", dockerfile=None, args=None):
    b = {"context": ctx}
    if dockerfile:
        b["dockerfile"] = dockerfile
    if args:
        b["args"] = args
    return {"image": name, "build": b}


class Collect(Scratch):
    def test_same_image_in_two_nodes_is_built_once(self):
        self.write("shared/Dockerfile", "FROM scratch\n")
        a, b = node(), node()
        for n in (a, b):
            n["apps"][0]["images"].append(built("app:1", "../../shared", args={"X": "1"}))
        self.make(nodes={"a": a, "b": b})
        dep = config.load_deployment(self.dep)
        found = images.collect([(n, n.apps) for n in dep.nodes])
        self.assertEqual(sorted(found), ["app:1", "nginx:1.27-alpine"])

    def test_other_build_args_is_a_conflict(self):
        self.write("shared/Dockerfile", "FROM scratch\n")
        a, b = node(), node()
        a["apps"][0]["images"].append(built("app:1", "../../shared", args={"API": "/a"}))
        b["apps"][0]["images"].append(built("app:1", "../../shared", args={"API": "/b"}))
        self.make(nodes={"a": a, "b": b})
        dep = config.load_deployment(self.dep)
        with self.assertRaises(BuildError) as cm:
            images.collect([(n, n.apps) for n in dep.nodes])
        self.assertIn("API=/a", str(cm.exception))
        self.assertIn("API=/b", str(cm.exception))

    def test_build_versus_pull_is_a_conflict(self):
        self.write("shared/Dockerfile", "FROM scratch\n")
        a, b = node(), node()
        a["apps"][0]["images"].append(built("app:1", "../../shared"))
        b["apps"][0]["images"].append({"image": "app:1", "pull": True})
        self.make(nodes={"a": a, "b": b})
        dep = config.load_deployment(self.dep)
        with self.assertRaises(BuildError):
            images.collect([(n, n.apps) for n in dep.nodes])


class Produce(Scratch):
    def test_commands(self):
        n = node()
        n["apps"][0]["images"] += [
            built("one:1", "ctx", "ctx/other.Dockerfile", {"VITE_BASE": "/app/", "VITE_API_URL": "/app/api"}),
            built("two:1", "ctx", None, {"ONE": "1"}),
            built("three:1", "ctx"),
        ]
        parts = {"compose.d/10-web.yml": "services:\n" + "".join(
            f"  s{i}:\n    image: {img}\n" for i, img in enumerate(["nginx:1.27-alpine", "one:1", "two:1", "three:1"]))}
        self.make(nodes={"test-node": n}, parts={"test-node": parts},
                  files={"nodes/test-node/ctx/Dockerfile": "FROM scratch\n",
                         "nodes/test-node/ctx/other.Dockerfile": "FROM scratch\n"})
        r = self.assertBuilds("--mode", "app")
        log = self.stub_log()
        ctx = str((self.dep / "nodes/test-node/ctx").resolve())
        self.assertIn(f"docker build --build-arg VITE_BASE=/app/ --build-arg VITE_API_URL=/app/api -t one:1 -f {ctx}/other.Dockerfile {ctx}", log)
        self.assertIn(f"docker build --build-arg ONE=1 -t two:1 {ctx}", log)
        self.assertIn(f"docker build -t three:1 {ctx}\n", log)
        self.assertIn("docker pull nginx:1.27-alpine", log)
        self.assertIn("docker save one:1 -o", log)
        self.assertIn("args  --build-arg VITE_BASE=/app/", r.stderr)

    def test_missing_context_is_caught_before_the_first_build(self):
        n = node()
        n["apps"][0]["images"].append(built("x:1", "nowhere"))
        self.make(nodes={"test-node": n})
        self.assertRefused("is not a directory", "--mode", "app")
        self.assertNotIn("docker build", self.stub_log())
        self.assertNotIn("docker pull", self.stub_log())
