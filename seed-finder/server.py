#!/usr/bin/env python3
"""Tiny static server for Seed Lab (development preview).

Usage:  python3 server.py [port]

Only needed for local testing — the site is fully static and can be served
by any web server. This server just makes sure .wasm files get the right
MIME type so WebAssembly streaming compilation works.
"""
import http.server
import sys

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8000


class Handler(http.server.SimpleHTTPRequestHandler):
    extensions_map = {
        **http.server.SimpleHTTPRequestHandler.extensions_map,
        ".wasm": "application/wasm",
        ".js": "text/javascript",
        ".mjs": "text/javascript",
        ".css": "text/css",
    }

    def end_headers(self):
        self.send_header("Cache-Control", "no-cache")
        super().end_headers()


if __name__ == "__main__":
    print(f"Seed Lab → http://localhost:{PORT}/")
    http.server.ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
