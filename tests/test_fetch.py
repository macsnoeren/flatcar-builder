"""Phase 2: downloads, against a local HTTP server."""

import http.server
import os
import threading
from pathlib import Path

from builder import fetch
from builder.errors import BuildError

from .helpers import Scratch


class Server(http.server.ThreadingHTTPServer):
    pass


class Handler(http.server.BaseHTTPRequestHandler):
    hits = []
    drop_first = set()

    def log_message(self, *a):
        pass

    def do_GET(self):
        Handler.hits.append(self.path)
        if self.path in Handler.drop_first:
            Handler.drop_first.discard(self.path)
            # Promise more than is sent, then hang up: an interrupted download.
            self.send_response(200)
            self.send_header("Content-Length", "1000")
            self.end_headers()
            self.wfile.write(b"partial")
            self.wfile.flush()
            self.connection.close()
            return
        files = {
            "/stable/amd64-usr/current/version.txt": b"FLATCAR_BUILD=1\nFLATCAR_VERSION=4152.2.0\n",
            "/stable/amd64-usr/4152.2.0/flatcar_production_image.bin.bz2": b"image",
            "/update/amd64-usr/4152.2.0/flatcar_production_update.gz": b"payload",
            "/compose/v2.39.1/docker-compose-linux-aarch64": b"compose-arm",
        }
        body = files.get(self.path)
        if body is None:
            self.send_response(404)
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class Fetch(Scratch):
    def setUp(self):
        super().setUp()
        Handler.hits = []
        Handler.drop_first = set()
        self.srv = Server(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        base = f"http://127.0.0.1:{self.srv.server_address[1]}"
        self.release = base + "/{channel}/{arch}/{version}"
        self.update = base + "/update/{arch}/{version}"
        self.compose = base + "/compose/{version}/docker-compose-linux-{arch}"
        self._env = os.environ.copy()
        os.environ["FLATCAR_BUILDER_CACHE"] = str(self.tmp / "dl-cache")
        for k in ("http_proxy", "HTTP_PROXY", "https_proxy", "HTTPS_PROXY"):
            os.environ.pop(k, None)

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()
        os.environ.clear()
        os.environ.update(self._env)
        super().tearDown()

    def test_current_is_looked_up(self):
        self.assertEqual(fetch.resolve_version("stable", "amd64-usr", "current", self.release), "4152.2.0")
        self.assertEqual(fetch.resolve_version("stable", "amd64-usr", "1.2.3", self.release), "1.2.3")

    def test_second_run_is_a_cache_hit(self):
        p = fetch.flatcar_image("stable", "amd64-usr", "4152.2.0", self.release, pause=0)
        self.assertEqual(p.read_bytes(), b"image")
        fetch.flatcar_image("stable", "amd64-usr", "4152.2.0", self.release, pause=0)
        self.assertEqual(len(Handler.hits), 1)

    def test_payload_falls_back_to_the_update_server(self):
        p = fetch.flatcar_payload("stable", "amd64-usr", "4152.2.0", self.release, self.update, pause=0)
        self.assertEqual(p.read_bytes(), b"payload")
        self.assertEqual(len(Handler.hits), 2)

    def test_interrupted_download_leaves_nothing_and_is_retried(self):
        path = "/stable/amd64-usr/4152.2.0/flatcar_production_image.bin.bz2"
        Handler.drop_first = {path}
        p = fetch.flatcar_image("stable", "amd64-usr", "4152.2.0", self.release, pause=0)
        self.assertEqual(p.read_bytes(), b"image")
        self.assertEqual(Handler.hits.count(path), 2)
        self.assertEqual([x.name for x in p.parent.iterdir()], [p.name])

    def test_missing_file_names_the_urls(self):
        with self.assertRaises(BuildError) as cm:
            fetch.flatcar_payload("stable", "amd64-usr", "1.0.0", self.release, self.update, pause=0)
        self.assertIn("HTTP 404", str(cm.exception))
        self.assertEqual(list((self.tmp / "dl-cache").rglob("*.part")), [])

    def test_compose_arch(self):
        p = fetch.compose_binary("v2.39.1", "arm64-usr", self.compose, pause=0)
        self.assertEqual(p.name, "docker-compose-v2.39.1-aarch64")
        with self.assertRaises(BuildError):
            fetch.compose_binary("v2.39.1", "riscv-usr", self.compose)
