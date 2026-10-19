"""Listening on a port: who may reach this server, and how it makes them prove it.

Over stdio there is nobody to authenticate: the agent started this process and reads its
standard output, so the operating system has already decided who is talking. A port is
the opposite — anything that can open a socket to it gets the whole power of the token
this server holds, which is the person's entire job search and, with ``--write``, the
right to change it. So the HTTP transports refuse to start without a shared secret, they
listen on the loopback address unless told otherwise, and DNS-rebinding protection is on
whatever they listen on, so a page in the person's browser cannot talk to the port on
their behalf (#3).

The secret is a bearer token of the person's own making, sent by the client as
``Authorization: Bearer …``. It is deliberately not OAuth: there is no authorisation
server here to discover, one person runs both ends, and a shared secret they can revoke by
restarting is honest about what this is. It must be strong (128 bits), it is compared in
constant time, and an address that keeps sending wrong ones is turned away (#21).
"""

from __future__ import annotations

import os
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from hmac import compare_digest
from typing import Any

import anyio
from mcp.server.auth.provider import AccessToken
from mcp.server.auth.settings import AuthSettings
from mcp.server.transport_security import TransportSecuritySettings
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from .client import PostuloError

#: The transports that listen on a port rather than on this process's own streams.
HTTP_TRANSPORTS = ("sse", "streamable-http")

#: Addresses only this machine can reach.
LOOPBACK = frozenset({"127.0.0.1", "localhost", "::1", "[::1]"})

#: Addresses that mean "every interface". Behind them the Host header a client sends is
#: whatever name it used to get here, which the server cannot guess (#13).
WILDCARDS = frozenset({"0.0.0.0", "::", "[::]", ""})  # noqa: S104 - names of them, not a bind

#: The environment variable holding the shared secret. A variable rather than an argument,
#: for the reason the address and the instance token are: a process list is readable.
SECRET_VARIABLE = "POSTULO_MCP_HTTP_TOKEN"  # noqa: S105 - the name of one, not one

#: Short enough to guess is not a secret. 128 bits is the floor: the 22 characters of a
#: ``token_urlsafe(16)``, with more than ten different ones among them, so ``aaaa…`` and a
#: sentence of dictionary words do not pass (#21).
MINIMUM_SECRET = 22
MINIMUM_DISTINCT = 11

#: A request body larger than this is refused. Tool arguments are small; a letter is at
#: most 40,000 characters.
MAX_REQUEST_BODY = 1024 * 1024

#: After this many wrong bearer secrets from one address within the window, that address
#: is answered 429 until the oldest has aged out (#21).
MISSES = 10
WINDOW = 300.0

#: The scope the shared secret stands for. One client, one power: reach this server.
SCOPE = "postulo-mcp"

NO_SECRET = (
    f"Serving over {{transport}} would put your whole job search on a port, so it needs a "
    f"secret the client must send. Set {SECRET_VARIABLE} to one — for example\n"
    '    python -c "import secrets; print(secrets.token_urlsafe(32))"\n'
    "— and have the agent send it as `Authorization: Bearer <that secret>`. Over stdio, "
    "which is what local agents use, none of this is needed."
)

SHORT_SECRET = (
    f"{SECRET_VARIABLE} is shorter than {MINIMUM_SECRET} characters, or uses too few "
    "different ones, which is a password somebody can guess rather than a secret. Make one "
    "with\n"
    '    python -c "import secrets; print(secrets.token_urlsafe(32))"'
)

NOT_PRINTABLE = (
    f"{SECRET_VARIABLE} holds a character that is not printable ASCII. An HTTP header "
    "cannot carry it reliably, so no client could send it back. Make one with\n"
    '    python -c "import secrets; print(secrets.token_urlsafe(32))"'
)

WILDCARD_HOST = (
    "--host {host} listens on every interface, and then the Host header a client sends is "
    "whatever name it reached this machine by, which cannot be guessed. Name at least one "
    "with --allowed-host (for example --allowed-host 192.168.1.5 or --allowed-host "
    "postulo-mcp.example)."
)


class SharedSecret:
    """Verifies one bearer token: the one the person set at both ends.

    An MCP ``TokenVerifier``, so the SDK's own middleware does the refusing — a request
    without a valid token never reaches a tool. The comparison is constant-time because
    the thing on the other side may be measuring.
    """

    def __init__(self, secret: str) -> None:
        self._secret = secret

    async def verify_token(self, token: str) -> AccessToken | None:
        # Bytes, not strings: compare_digest raises on a str with a non-ASCII character, and
        # a header can carry one, so a stranger could turn every refusal into a 500 (#14).
        if not compare_digest(token.encode("utf-8", "replace"), self._secret.encode("utf-8")):
            return None
        return AccessToken(token=token, client_id="postulo-mcp", scopes=[SCOPE])


@dataclass(frozen=True)
class Listening:
    """What a transport that listens on a port needs before it may."""

    host: str
    port: int
    secret: str
    allowed_hosts: tuple[str, ...] = ()
    transport: str = "streamable-http"

    @property
    def loopback(self) -> bool:
        return self.host in LOOPBACK

    @property
    def address(self) -> str:
        """``host:port``, with an IPv6 address in the brackets a URL wants."""
        host = f"[{self.host}]" if ":" in self.host and not self.host.startswith("[") else self.host
        return f"{host}:{self.port}"

    @property
    def verifier(self) -> SharedSecret:
        return SharedSecret(self.secret)

    @property
    def auth(self) -> AuthSettings:
        """Enough of the OAuth settings for the SDK to require a bearer token.

        There is no authorisation server behind ``issuer_url``: a client that goes looking
        for one finds nothing, and sends the secret as a header instead. The value is here
        because the SDK asks for it, and it names the only place a token for this server
        could have come from — this server's own operator.
        """
        return AuthSettings(
            issuer_url=f"http://{self.address}",  # type: ignore[arg-type]
            resource_server_url=f"http://{self.address}",  # type: ignore[arg-type]
            required_scopes=[SCOPE],
            # The one secret is not bound to a resource: nothing here issues tokens.
            validate_token_resource=False,
        )

    @property
    def security(self) -> TransportSecuritySettings:
        """Which ``Host`` and ``Origin`` headers are this server's own address.

        Without this, a page the person is browsing could post to ``localhost`` and read
        their job search through their own machine. A reverse proxy in front of this needs
        its name added with ``--allowed-host``, because the header then says the proxy.
        """
        hosts = [self.address]
        for entry in self.allowed_hosts:
            hosts += accepted_forms(entry)
        if self.loopback:
            hosts += [f"127.0.0.1:{self.port}", f"localhost:{self.port}", f"[::1]:{self.port}"]
        seen = list(dict.fromkeys(hosts))
        return TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=seen,
            allowed_origins=[f"{scheme}://{host}" for host in seen for scheme in ("http", "https")],
        )

    def run_arguments(self) -> dict[str, Any]:
        """What the app behind this port is built with.

        Streamable HTTP runs stateless: nothing here remembers a client between calls, and
        the SDK's stateful mode kept every session it ever opened (CVE-2026-59951, fixed in
        mcp 2.2.0). Stateless costs nothing and holds even should such a fault come back (#17).
        """
        arguments: dict[str, Any] = {
            "transport_security": self.security,
            "max_request_body_size": MAX_REQUEST_BODY,
            "host": self.host,
        }
        if self.transport == "streamable-http":
            arguments["stateless_http"] = True
        return arguments


def accepted_forms(entry: str) -> list[str]:
    """The ``Host`` values a client may send for one ``--allowed-host``.

    The SDK compares a header with the list exactly, or against ``name:*``. A client that
    reaches ``https://proxy.example`` sends ``Host: proxy.example`` with no port, so a bare
    name allows the name alone and with any port; ``name:443`` and ``name:80`` also allow
    the bare name, which is what a client sends for a default port (#13).
    """
    entry = entry.strip()
    if not entry:
        return []
    if entry.startswith("["):
        name, _, port = entry.rpartition("]:")
        if not name:  # a bracketed address with no port
            return [entry, f"{entry}:*"]
        name += "]"
    elif entry.count(":") == 1:
        name, _, port = entry.partition(":")
    else:  # a bare name, or a bare IPv6 address
        bare = f"[{entry}]" if ":" in entry else entry
        return [bare, f"{bare}:*"]
    if port == "*":
        return [entry, name]
    forms = [entry]
    if port in ("80", "443"):
        forms.append(name)
    return forms


class Throttle:
    """An ASGI wrapper that stops answering an address that keeps sending wrong secrets.

    The SDK's bearer middleware answers each request on its own, so a wrong secret costs
    nothing and the next guess is free. This counts the 401s that come back, per client
    address, and answers 429 once an address has had too many within the window. It sits
    outside everything else, so a barred address never reaches the secret check at all.
    Behind a reverse proxy every client has the proxy's address, so the bar then falls on
    the proxy; throttle at the proxy as well.
    """

    def __init__(
        self,
        app: ASGIApp,
        *,
        misses: int = MISSES,
        window: float = WINDOW,
        clock: Callable[[], float] = time.monotonic,
        capacity: int = 4096,
    ) -> None:
        self.app = app
        self.misses = misses
        self.window = window
        self.clock = clock
        self.capacity = capacity
        self._seen: dict[str, deque[float]] = {}

    def wait(self, who: str) -> int:
        """Seconds until ``who`` may try again, 0 where it may now."""
        times = self._seen.get(who)
        if not times:
            return 0
        now = self.clock()
        while times and now - times[0] >= self.window:
            times.popleft()
        if len(times) < self.misses:
            return 0
        return max(1, int(times[0] + self.window - now) + 1)

    def miss(self, who: str) -> None:
        if who not in self._seen and len(self._seen) >= self.capacity:
            now = self.clock()
            for other in [k for k, v in self._seen.items() if not v or now - v[-1] >= self.window]:
                del self._seen[other]
            if len(self._seen) >= self.capacity:
                del self._seen[next(iter(self._seen))]
        self._seen.setdefault(who, deque()).append(self.clock())

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        client = scope.get("client")
        who = str(client[0]) if client else "unknown"
        wait = self.wait(who)
        if wait:
            body = b'{"detail":"Too many wrong secrets from this address. Try again later."}'
            await send(
                {
                    "type": "http.response.start",
                    "status": 429,
                    "headers": [
                        (b"content-type", b"application/json"),
                        (b"content-length", str(len(body)).encode()),
                        (b"retry-after", str(wait).encode()),
                    ],
                }
            )
            await send({"type": "http.response.body", "body": body})
            return

        async def counting(message: Message) -> None:
            if message["type"] == "http.response.start" and message["status"] == 401:
                self.miss(who)
            await send(message)

        await self.app(scope, receive, counting)


def app_for(server: Any, listening: Listening) -> ASGIApp:
    """The server's ASGI app for this transport, with the throttle outside it."""
    arguments = listening.run_arguments()
    if listening.transport == "sse":
        app = server.sse_app(**arguments)
    else:
        app = server.streamable_http_app(**arguments)
    return Throttle(app)


def serve(server: Any, listening: Listening) -> None:
    """Listen on the port until stopped."""
    import uvicorn

    config = uvicorn.Config(
        app_for(server, listening),
        host=listening.host,
        port=listening.port,
        log_level=server.settings.log_level.lower(),
    )
    anyio.run(uvicorn.Server(config).serve)


def plan(
    transport: str,
    *,
    host: str = "127.0.0.1",
    port: int = 8000,
    allowed_hosts: list[str] | None = None,
    environment: dict[str, str] | None = None,
) -> Listening | None:
    """How to listen, or ``None`` for stdio; a refusal in words for a port left open."""
    if transport not in HTTP_TRANSPORTS:
        return None
    env = os.environ if environment is None else environment
    secret = env.get(SECRET_VARIABLE, "").strip()
    if not secret:
        raise PostuloError(NO_SECRET.format(transport=transport))
    if not (secret.isascii() and secret.isprintable()):
        raise PostuloError(NOT_PRINTABLE)
    if len(secret) < MINIMUM_SECRET or len(set(secret)) < MINIMUM_DISTINCT:
        raise PostuloError(SHORT_SECRET)
    if host.strip().lower() in WILDCARDS and not allowed_hosts:
        raise PostuloError(WILDCARD_HOST.format(host=host or "(empty)"))
    return Listening(
        host=host,
        port=port,
        secret=secret,
        allowed_hosts=tuple(allowed_hosts or ()),
        transport=transport,
    )


__all__ = [
    "HTTP_TRANSPORTS",
    "SCOPE",
    "Listening",
    "SharedSecret",
    "Throttle",
    "app_for",
    "plan",
    "serve",
]
