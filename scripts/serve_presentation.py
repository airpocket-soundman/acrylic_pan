"""Serve the web/ folder without browser caching, so edits to the deck show up on reload."""
import argparse
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


class NoStoreHandler(SimpleHTTPRequestHandler):
    def end_headers(self) -> None:
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def log_message(self, format: str, *args) -> None:
        return


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8780)
    parser.add_argument("--bind", default="127.0.0.1")
    parser.add_argument("--directory", default=str(Path(__file__).resolve().parents[1] / "web"))
    args = parser.parse_args()
    handler = partial(NoStoreHandler, directory=args.directory)
    ThreadingHTTPServer((args.bind, args.port), handler).serve_forever()


if __name__ == "__main__":
    main()
