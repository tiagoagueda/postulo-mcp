"""The instance, as seen from outside: one HTTP client, one token, plain dictionaries.

Everything this server can do, a person can do with `curl` and the same token. There is
no privileged path: the API decides what the token may read and write, and this client
only ever asks. Errors come back as sentences rather than exceptions, because the thing
reading them is a model that will otherwise invent an explanation.
"""

from __future__ import annotations

import ipaddress
import json
import os
import re
import time
from dataclasses import dataclass
from urllib.parse import urlsplit

import httpx
from mcp.server.mcpserver.exceptions import ToolError

from . import __version__
from .fence import fence

USER_AGENT = f"postulo-mcp/{__version__}"

#: How long one wait (connecting, one read, one write) may take.
TIMEOUT = 20.0

#: How long a whole exchange may take, and how much of an answer is read. The per-wait
#: timeout bounds neither: a body dripped a byte at a time never trips it, and a client that
#: reads whatever arrives buffers whatever arrives (#18). The API bounds its own lists.
TOTAL = 60.0
MAX_BODY = 8 * 1024 * 1024

#: How many field errors of a refused request are passed on.
MAX_ERRORS = 5

_SCOPE = re.compile(r"[a-z][a-z0-9:_.-]{0,39}")


class PostuloError(ToolError):
    """The instance said no, in words the agent can pass on.

    A ``ToolError`` on purpose. The SDK treats any other exception out of a tool as a crash
    and tells the model only "Error executing tool …", so before this a refusal never
    reached the agent at all, whatever it said. A ``ToolError`` carries its message through
    as the tool's error result, which is why everything in it that somebody else wrote is
    fenced first (#19).
    """


@dataclass(frozen=True)
class Settings:
    """Where the instance is, which token to use, and whether writing is allowed at all."""

    url: str
    token: str
    read_only: bool = True
    verify: bool = True

    @classmethod
    def from_environment(cls, *, read_only: bool | None = None) -> Settings:
        """The environment's answer, with ``read_only`` given only when a flag decided it.

        ``POSTULO_MCP_READ_ONLY`` is the answer when nothing on the command line said
        otherwise, which is what ``read_only=None`` means here: ``--write`` passes
        ``False`` and settles it, and its absence leaves the environment to speak. It used
        to be passed either way, so the variable was read and then always overruled (#3).

        ``POSTULO_MCP_INSECURE`` turns off TLS certificate verification for the connection
        to the instance. It exists for one case — a self-signed certificate on a machine of
        your own — and it means anything on the path between here and Postulo can read the
        token and rewrite the answers. It is documented in the README with that warning,
        and ``__main__`` says so on stderr every time it starts with it on.
        """
        url = os.environ.get("POSTULO_URL", "").rstrip("/")
        token = os.environ.get("POSTULO_TOKEN", "")
        if not url:
            raise PostuloError("POSTULO_URL is not set: the address of your Postulo instance.")
        if not token:
            raise PostuloError("POSTULO_TOKEN is not set: a personal access token from Settings.")
        # Unset means read-only: an agent gets to look before anybody decides it may write.
        flag = os.environ.get("POSTULO_MCP_READ_ONLY", "").strip().lower()
        if read_only is None:
            read_only = flag not in {"0", "false", "no"}
        return cls(
            url=url,
            token=token,
            read_only=read_only,
            verify=os.environ.get("POSTULO_MCP_INSECURE", "").strip().lower()
            not in {"1", "true", "yes"},
        )


class Postulo:
    """One instance's API, over one token."""

    def __init__(self, settings: Settings, *, transport: httpx.BaseTransport | None = None) -> None:
        self.settings = settings
        self.client = httpx.Client(
            base_url=f"{settings.url}/api/v1",
            headers={
                "Authorization": f"Bearer {settings.token}",
                "Accept": "application/json",
                "User-Agent": USER_AGENT,
            },
            timeout=TIMEOUT,
            verify=settings.verify,
            transport=transport,
            follow_redirects=False,
        )

    def close(self) -> None:
        self.client.close()

    # --------------------------------------------------------------- calling

    def get(self, path: str, **params):
        return self._call(
            "GET", path, params={k: v for k, v in params.items() if v not in (None, "")}
        )

    def post(self, path: str, payload: dict | None = None):
        self._refuse_writes()
        return self._call("POST", path, json=payload or {})

    def patch(self, path: str, payload: dict):
        self._refuse_writes()
        return self._call("PATCH", path, json=payload)

    def _refuse_writes(self) -> None:
        if self.settings.read_only:
            raise PostuloError(
                "This server is running read-only, so it will not change anything. "
                "The person running it can start it with --write."
            )

    def _call(self, method: str, path: str, **kwargs):
        deadline = time.monotonic() + TOTAL
        try:
            with self.client.stream(method, path, **kwargs) as response:
                status = response.status_code
                headers = response.headers
                body = _read(response, deadline)
        except httpx.HTTPError as error:
            raise PostuloError(
                f"Could not reach Postulo ({type(error).__name__}); what the connection said:\n"
                f"{fence(str(error)[:300], what='error')}"
            ) from error

        if 300 <= status < 400:
            raise PostuloError(_redirect(status, headers.get("location", "")))
        if status == 401:
            raise PostuloError("Postulo refused the token. It may be revoked or expired (401).")
        if status >= 400:
            raise PostuloError(_refusal(status, headers, body))
        if not body:
            return {}
        if not _is_json(headers.get("content-type", "")):
            raise PostuloError(
                f"Postulo answered {status} with something other than JSON "
                f"({_label(headers.get('content-type', ''))}), which is not its API: a login "
                "page or a proxy in front of it, perhaps. Nothing was recorded or read. "
                "Check POSTULO_URL."
            )
        try:
            return json.loads(body)
        except ValueError as error:
            raise PostuloError(
                f"Postulo answered {status} with JSON that does not parse. Nothing was read."
            ) from error


def _read(response: httpx.Response, deadline: float) -> bytes:
    """The body, read in pieces under a ceiling and a deadline for the whole exchange."""
    declared = response.headers.get("content-length", "")
    if declared.isdecimal() and int(declared) > MAX_BODY:
        raise PostuloError("Postulo's answer is too large to read.")
    body = bytearray()
    for chunk in response.iter_bytes():
        body += chunk
        if len(body) > MAX_BODY or time.monotonic() > deadline:
            raise PostuloError("Postulo's answer is too large or too slow to read.")
    return bytes(body)


def _is_json(content_type: str) -> bool:
    kind = content_type.split(";")[0].strip().lower()
    return kind == "application/json" or kind.endswith("+json")


def _label(content_type: str) -> str:
    """A content type, as much of it as is safe to repeat."""
    kind = content_type.split(";")[0].strip().lower()
    return kind if re.fullmatch(r"[a-z0-9.+/_-]{1,60}", kind) else "no usable content type"


def _redirect(status: int, location: str) -> str:
    sentence = (
        f"Postulo answered {status}, a redirect, so nothing was read or recorded. "
        "Set POSTULO_URL to the address it redirects to"
    )
    if location:
        return f"{sentence}; where it points:\n{fence(location[:300], what='error')}"
    return f"{sentence}."


def _refusal(status: int, headers: httpx.Headers, body: bytes) -> str:
    """What Postulo said no with, in a sentence of ours and the answer's words in a fence.

    A problem document (RFC 9457) carries what a client can act on: the scope a token
    lacks, what is wrong with each field, how long to wait (#15). Whatever the body says is
    fenced, because the thing that answered may not be Postulo (#19); only what this code
    checks itself — a status, a scope name, a number of seconds — stands outside.
    """
    problem = _problem(body)
    lines: list[str] = []
    sentence = f"Postulo answered {status}"
    if status == 403:
        sentence += " (the token may lack a scope this call needs)"
    if problem is None:
        text = body.decode("utf-8", "replace").strip()[:300]
        if text:
            lines.append(text)
    else:
        for key in ("detail", "message", "error"):
            if isinstance(problem.get(key), str) and problem[key]:
                lines.append(problem[key][:300])
                break
        errors = problem.get("errors")
        if isinstance(errors, list):
            for entry in errors[:MAX_ERRORS]:
                lines.append(_error_line(entry))
            if len(errors) > MAX_ERRORS:
                lines.append(f"… and {len(errors) - MAX_ERRORS} more")
        scope = problem.get("scope")
        if status == 403 and isinstance(scope, str) and _SCOPE.fullmatch(scope):
            sentence += f"; the scope it names is {scope!r}"
        retry = _seconds(problem.get("retry_after"), headers.get("retry-after", ""))
        if retry is not None:
            sentence += f"; try again in {retry} seconds"
    if status == 404 and not lines:
        return "Postulo has no such thing."
    if not lines:
        return f"{sentence}."
    return f"{sentence}; what it said:\n{fence(chr(10).join(lines), what='error')}"


def _problem(body: bytes) -> dict | None:
    try:
        parsed = json.loads(body)
    except ValueError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _error_line(entry: object) -> str:
    if not isinstance(entry, dict):
        return str(entry)[:200]
    where = entry.get("loc")
    place = ".".join(str(step) for step in where) if isinstance(where, list) else ""
    message = str(entry.get("msg", entry.get("message", "")))[:200]
    return f"{place}: {message}" if place else message


def _seconds(*candidates: object) -> int | None:
    """A wait in whole seconds from the first candidate that is a plausible number."""
    for candidate in candidates:
        try:
            value = float(candidate)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            continue
        if 0 <= value <= 86400:
            return int(value) + (value % 1 > 0)
    return None


def plain_http_warning(url: str) -> str | None:
    """A sentence for stderr when the token would cross a network in clear, else ``None``.

    Not a refusal: the project supports plain HTTP inside a private mesh. But the person
    ought to hear it every time, as they do for ``POSTULO_MCP_INSECURE`` (#7).
    """
    parts = urlsplit(url)
    if parts.scheme != "http" or not parts.hostname:
        return None
    host = parts.hostname
    if host == "localhost" or host.endswith(".localhost"):
        return None
    try:
        if ipaddress.ip_address(host).is_loopback:
            return None
    except ValueError:
        pass
    return (
        f"POSTULO_URL is {url}, which is plain http to a host that is not this machine: your "
        "token travels unencrypted on every call, and an instance that redirects http to "
        "https will answer nothing useful. Use https unless this is a private network you trust."
    )
