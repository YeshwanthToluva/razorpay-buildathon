"""Static server for the console with caching disabled.

python -m http.server caches aggressively, so an edited console can keep serving
the previous build and look as though a change did not apply. This sends
no-store, which removes that whole class of confusion during a demo.

    python apps/serve_ui.py [port]
"""

from __future__ import annotations

import functools
import http.server
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1] / "ui"


class NoCacheHandler(http.server.SimpleHTTPRequestHandler):
    def end_headers(self):
        self.send_header("Cache-Control", "no-store, must-revalidate")
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")
        super().end_headers()

    def log_message(self, fmt, *args):  # quiet
        pass


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 3000
    handler = functools.partial(NoCacheHandler, directory=str(ROOT))
    print(f"console on http://localhost:{port}  (no-cache)")
    http.server.ThreadingHTTPServer(("127.0.0.1", port), handler).serve_forever()
