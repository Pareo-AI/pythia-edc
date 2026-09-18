#!/usr/bin/env python3
"""Demo data mock server.

Serves the demo datasets defined in ``datasets.py`` (the single source of
truth) at GET /{provider}/{dataset} — as JSON, or verbatim for a file-backed
dataset (``Dataset.file_name``, e.g. the kupferwerk Turtle assets) — and falls
back to any static sample in ``data/`` at GET /{name}.
"""
import http.server
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import datasets

PORT = int(os.environ.get("MOCK_PORT", "9876"))
BIND = os.environ.get("MOCK_BIND", "0.0.0.0")
DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")

# Extensions mimetypes.guess_type doesn't reliably map (notably .ttl); explicit
# beats a stdlib guess that varies by OS mimetypes.conf.
_CONTENT_TYPE_BY_EXT = {".json": "application/json", ".ttl": "text/turtle"}


class DemoHandler(http.server.BaseHTTPRequestHandler):
    """Serve module datasets at /{provider}/{dataset}; static files at /{name}."""

    def do_GET(self):
        # Strip leading/trailing slashes and any query string or fragment
        path = self.path.strip("/").split("?")[0].split("#")[0]

        if not path:
            self._send_json(200, {"status": "ok", "datasets": self._list_datasets()})
            return

        # 1. Module-defined dataset ("provider/dataset") — JSON payload, or a
        # verbatim file (dataset.file_name) served as dataset.content_type.
        dataset = datasets.find_dataset(path)
        if dataset is not None:
            if dataset.file_name is not None:
                self._send_file(os.path.join(DATA_DIR, dataset.file_name), dataset.content_type)
            else:
                self._send_json(200, dataset.payload())
            return

        # 2. Static sample in data/, tried per known extension.
        static_file = self._resolve_static_file(path)
        if static_file is None:
            self._send_error(404, f"Dataset '{path}' not found")
            return
        content_type = _CONTENT_TYPE_BY_EXT[os.path.splitext(static_file)[1]]
        self._send_file(static_file, content_type)

    def _resolve_static_file(self, path: str) -> str | None:
        """Resolve `path` to a file under DATA_DIR by trying each known extension.

        Rejects any candidate that would resolve outside DATA_DIR (path traversal).
        """
        data_dir_real = os.path.realpath(DATA_DIR)
        for ext in _CONTENT_TYPE_BY_EXT:
            candidate = os.path.realpath(os.path.join(DATA_DIR, f"{path}{ext}"))
            if os.path.dirname(candidate) != data_dir_real:
                continue
            if os.path.isfile(candidate):
                return candidate
        return None

    def _send_file(self, file_path: str, content_type: str) -> None:
        try:
            with open(file_path, "rb") as f:
                data = f.read()
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            print(f"[mock-server] 200 GET {self.path}", flush=True)
        except OSError as e:
            self._send_error(500, str(e))

    def _list_datasets(self):
        paths = datasets.all_mock_paths()
        if os.path.isdir(DATA_DIR):
            paths += [f[:-5] for f in os.listdir(DATA_DIR) if f.endswith(".json")]
        return paths

    def _send_json(self, status: int, body: dict):
        data = json.dumps(body, indent=2)
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data.encode("utf-8"))))
        self.end_headers()
        self.wfile.write(data.encode("utf-8"))
        print(f"[mock-server] {status} GET {self.path}", flush=True)

    def _send_error(self, status: int, message: str):
        body = json.dumps({"error": message})
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body.encode("utf-8"))))
        self.end_headers()
        self.wfile.write(body.encode("utf-8"))
        print(f"[mock-server] {status} GET {self.path} — {message}", flush=True)

    def log_message(self, format, *args):
        # Suppress the default access log (we log manually above)
        pass


def run(port: int = PORT):
    """Start the mock server (blocking)."""
    server = http.server.HTTPServer((BIND, port), DemoHandler)
    print(f"[mock-server] Listening on http://{BIND}:{port}/  (data dir: {DATA_DIR})", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[mock-server] Shutting down.", flush=True)
        server.server_close()


if __name__ == "__main__":
    run()
