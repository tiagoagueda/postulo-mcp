"""Starting the server: read-only unless told otherwise, over stdio unless told otherwise.

An agent launches this as a subprocess and talks to it over standard input and output,
which is what local agents speak. The address and the token come from the environment, so
neither ever appears in a command line or a process list.
"""

from __future__ import annotations

import argparse
import sys

from . import __version__
from .client import Postulo, PostuloError, Settings
from .server import build


def parse(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="postulo-mcp",
        description=(
            "Serve one Postulo instance to an AI agent over the Model Context Protocol. "
            "Set POSTULO_URL and POSTULO_TOKEN in the environment."
        ),
    )
    parser.add_argument(
        "--write",
        action="store_true",
        help=(
            "Allow the agent to record notes, statuses, reminders and letter drafts. "
            "Off by default; the token must also carry the write scope."
        ),
    )
    parser.add_argument(
        "--transport",
        default="stdio",
        choices=("stdio", "sse", "streamable-http"),
        help="How the agent talks to this server. stdio is what local agents use.",
    )
    parser.add_argument("--version", action="version", version=f"postulo-mcp {__version__}")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    options = parse(argv)
    try:
        settings = Settings.from_environment(read_only=not options.write)
    except PostuloError as error:
        print(error, file=sys.stderr)
        return 2
    postulo = Postulo(settings)
    try:
        build(postulo).run(transport=options.transport)
    finally:
        postulo.close()
    return 0


if __name__ == "__main__":  # pragma: no cover - the console entry point
    raise SystemExit(main())
