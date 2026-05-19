#!/usr/bin/env python3
"""Demo data mock server.

Serves the CO2 demo datasets defined in ``datasets.py`` (the single source of
truth) at GET /{provider}/{dataset}, and falls back to any static JSON sample in
``data/`` at GET /{name}.
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


class DemoHandler(http.server.BaseHTTPRequestHandler):
    """Serve module datasets at /{provider}/{dataset}; static files at /{name}."""

    def do_GET(self):
        # Strip leading/trailing slashes and any query string or fragment
        path = self.path.strip("/").split("?")[0].split("#")[0]

        if not path:
            self._send_json(200, {"status": "ok", "datasets": self._list_datasets()})
            return

        # 1. Module-defined dataset ("provider/dataset")
        payload = datasets.find_payload(path)
        if payload is not None:
            self._send_json(200, payload)
            return

        # 2. Static JSON sample in data/
        json_file = os.path.join(DATA_DIR, f"{path}.json")
        if not os.path.isfile(json_file):
            self._send_error(404, f"Dataset '{path}' not found")
            return

        try:
            with open(json_file, encoding="utf-8") as f:
                data = f.read()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data.encode("utf-8"))))
            self.end_headers()
            self.wfile.write(data.encode("utf-8"))
            print(f"[mock-server] 200 GET /{path}", flush=True)
        except Exception as e:
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
