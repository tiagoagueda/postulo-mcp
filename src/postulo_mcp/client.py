"""The instance, as seen from outside: one HTTP client, one token, plain dictionaries.

Everything this server can do, a person can do with `curl` and the same token. There is
no privileged path: the API decides what the token may read and write, and this client
only ever asks. Errors come back as sentences rather than exceptions, because the thing
reading them is a model that will otherwise invent an explanation.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

import httpx

from . import __version__

USER_AGENT = f"postulo-mcp/{__version__}"
TIMEOUT = 20.0


class PostuloError(Exception):
    """The instance said no, in words the agent can pass on."""


@dataclass(frozen=True)
class Settings:
    """Where the instance is, which token to use, and whether writing is allowed at all."""

    url: str
    token: str
    read_only: bool = True
    verify: bool = True

    @classmethod
    def from_environment(cls, *, read_only: bool | None = None) -> Settings:
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
        try:
            response = self.client.request(method, path, **kwargs)
        except httpx.HTTPError as error:
            raise PostuloError(f"Could not reach Postulo: {error}") from error
        if response.status_code in (401, 403):
            raise PostuloError(
                "Postulo refused the token. It may be revoked, expired, or without the "
                f"scope this needs ({response.status_code})."
            )
        if response.status_code == 404:
            raise PostuloError("Postulo has no such thing.")
        if response.status_code >= 400:
            raise PostuloError(f"Postulo answered {response.status_code}: {_detail(response)}")
        if not response.content:
            return {}
        try:
            return response.json()
        except ValueError:
            return {"text": response.text}


def _detail(response: httpx.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        return response.text[:200]
    if isinstance(body, dict):
        for key in ("detail", "message", "error"):
            if key in body:
                return str(body[key])[:300]
    return str(body)[:300]
