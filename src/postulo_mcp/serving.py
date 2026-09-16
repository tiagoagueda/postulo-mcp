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
restarting is honest about what this is.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from hmac import compare_digest
from typing import Any

from mcp.server.auth.provider import AccessToken
from mcp.server.auth.settings import AuthSettings
from mcp.server.transport_security import TransportSecuritySettings

from .client import PostuloError

#: The transports that listen on a port rather than on this process's own streams.
HTTP_TRANSPORTS = ("sse", "streamable-http")

#: Addresses only this machine can reach.
LOOPBACK = frozenset({"127.0.0.1", "localhost", "::1", "[::1]"})

#: The environment variable holding the shared secret. A variable rather than an argument,
#: for the reason the address and the instance token are: a process list is readable.
SECRET_VARIABLE = "POSTULO_MCP_HTTP_TOKEN"  # noqa: S105 - the name of one, not one

#: Short enough to guess is not a secret. Sixteen characters of anything is the floor.
MINIMUM_SECRET = 16

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
    f"{SECRET_VARIABLE} is shorter than {MINIMUM_SECRET} characters, which is a password "
    "somebody can guess rather than a secret. Make a longer one with\n"
    '    python -c "import secrets; print(secrets.token_urlsafe(32))"'
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
        if not compare_digest(token, self._secret):
            return None
        return AccessToken(token=token, client_id="postulo-mcp", scopes=[SCOPE])


@dataclass(frozen=True)
class Listening:
    """What a transport that listens on a port needs before it may."""

    host: str
    port: int
    secret: str
    allowed_hosts: tuple[str, ...] = ()

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
        )

    @property
    def security(self) -> TransportSecuritySettings:
        """Which ``Host`` and ``Origin`` headers are this server's own address.

        Without this, a page the person is browsing could post to ``localhost`` and read
        their job search through their own machine. A reverse proxy in front of this needs
        its name added with ``--allowed-host``, because the header then says the proxy.
        """
        hosts = [self.address, *self.allowed_hosts]
        if self.loopback:
            hosts += [f"127.0.0.1:{self.port}", f"localhost:{self.port}", f"[::1]:{self.port}"]
        seen = list(dict.fromkeys(hosts))
        return TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=seen,
            allowed_origins=[f"{scheme}://{host}" for host in seen for scheme in ("http", "https")],
        )

    def run_arguments(self) -> dict[str, Any]:
        """What to hand ``MCPServer.run`` beside the transport's name."""
        return {"host": self.host, "port": self.port, "transport_security": self.security}


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
    if len(secret) < MINIMUM_SECRET:
        raise PostuloError(SHORT_SECRET)
    return Listening(host=host, port=port, secret=secret, allowed_hosts=tuple(allowed_hosts or ()))


__all__ = ["HTTP_TRANSPORTS", "SCOPE", "Listening", "SharedSecret", "plan"]
