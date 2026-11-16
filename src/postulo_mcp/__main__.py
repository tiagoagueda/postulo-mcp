"""Starting the server: read-only unless told otherwise, over stdio unless told otherwise.

An agent launches this as a subprocess and talks to it over standard input and output,
which is what local agents speak. The address and the token come from the environment, so
neither ever appears in a command line or a process list.

The other transports listen on a port, and a port is a different proposition: `serving`
holds what they must satisfy first. ``--write`` can be narrowed to the kinds of writing
you meant — ``--write=notes,reminders`` leaves statuses alone — and when it is not given
at all, ``POSTULO_MCP_READ_ONLY`` is what decides, which it did not use to (#3).
"""

from __future__ import annotations

import argparse
import sys

from . import __version__, serving
from .client import Postulo, PostuloError, Settings, plain_http_warning
from .server import WRITE_KINDS, build


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
        nargs="?",
        const="all",
        default="",
        metavar="KINDS",
        help=(
            "Allow the agent to record notes, statuses, reminders, letter drafts and captures. "
            "Off "
            "by default; the token must also carry the write scope. Narrow it with a "
            "comma-separated list — --write=notes,reminders — to leave the rest read-only. "
            f"The kinds are {', '.join(WRITE_KINDS)}."
        ),
    )
    parser.add_argument(
        "--transport",
        default="stdio",
        choices=("stdio", "sse", "streamable-http"),
        help="How the agent talks to this server. stdio is what local agents use.",
    )
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help=(
            "Which address to listen on, for the transports that listen at all. Loopback "
            "by default: anything else is reachable from the network, and everything that "
            "reaches it needs the bearer token."
        ),
    )
    parser.add_argument("--port", type=int, default=8000, help="Which port to listen on.")
    parser.add_argument(
        "--allowed-host",
        action="append",
        default=[],
        metavar="HOST",
        help=(
            "An extra Host header to accept, for a reverse proxy in front of this, or for "
            "the name clients use when --host is 0.0.0.0. A name without a port allows the "
            "name with and without one; the address being listened on is accepted already."
        ),
    )
    parser.add_argument("--version", action="version", version=f"postulo-mcp {__version__}")
    return parser.parse_args(argv)


def write_kinds(value: str) -> frozenset[str] | None:
    """What ``--write`` asked for: nothing, everything (``None``), or the kinds named."""
    value = value.strip().lower()
    if not value:
        return frozenset()
    if value == "all":
        return None
    kinds = frozenset(part.strip() for part in value.split(",") if part.strip())
    unknown = sorted(kinds - set(WRITE_KINDS))
    if unknown:
        raise PostuloError(
            f"--write does not know {', '.join(unknown)}. The kinds are "
            f"{', '.join(WRITE_KINDS)}, or --write on its own for all of them."
        )
    return kinds


def main(argv: list[str] | None = None) -> int:
    options = parse(argv)
    try:
        requested = write_kinds(options.write)
        listening = serving.plan(
            options.transport,
            host=options.host,
            port=options.port,
            allowed_hosts=options.allowed_host,
        )
        # Only a flag settles it; without one the environment is asked, and its default
        # is read-only. Passing it either way is what made POSTULO_MCP_READ_ONLY dead.
        settings = Settings.from_environment(read_only=False if requested != frozenset() else None)
    except PostuloError as error:
        print(error, file=sys.stderr)
        return 2

    if not settings.verify:
        print(
            "POSTULO_MCP_INSECURE is set: TLS certificates are not being checked, so "
            "anything between here and Postulo can read your token and change the "
            "answers. Use it only against a certificate of your own.",
            file=sys.stderr,
        )
    warning = plain_http_warning(settings.url)
    if warning:
        print(warning, file=sys.stderr)
    if listening is not None and not listening.loopback:
        print(
            f"Listening on {listening.address}, which is not only this machine. Every "
            "request must carry the bearer token; put TLS in front of it.",
            file=sys.stderr,
        )

    postulo = Postulo(settings)
    try:
        server = build(
            postulo,
            writes=requested or None,
            auth=listening.auth if listening else None,
            token_verifier=listening.verifier if listening else None,
        )
        if listening is None:
            server.run(transport="stdio")
        else:
            serving.serve(server, listening)
    finally:
        postulo.close()
    return 0


if __name__ == "__main__":  # pragma: no cover - the console entry point
    raise SystemExit(main())
