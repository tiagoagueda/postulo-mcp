"""A Postulo API that lives in memory and is held to the real one's OpenAPI description.

The fake this replaces accepted any parameter at any address, so two tools could send
``shortlisted`` and ``upcoming`` — which the API has never had — and the tests agreed with
them all the way to a user asking for their shortlist and getting the undecided pile (#3).

So the fake is built from `openapi.json`, a snapshot of the instance's own description,
and it refuses what the instance would ignore: an address that does not exist, a method it
does not have, a query parameter it does not take, a value of the wrong type. It answers
in the shape the description promises, which for a list means the paginated envelope —
``{"items": …, "count": …}`` — rather than the bare array the old fake handed back.

Refusals are `AssertionError`, not HTTP errors: a tool sending a parameter into the void
is a fault in this repository, and it should read as a failed test rather than as an
instance that happened to say no.

To refresh the snapshot, from a checkout of the core:

    DJANGO_SETTINGS_MODULE=postulo.config.settings.test uv run python -c "
    import django, json, sys; django.setup()
    from postulo.api.api import api
    print(json.dumps(api.get_openapi_schema(), indent=2, sort_keys=True, default=str))
    " > ../postulo-mcp/tests/openapi.json

The snapshot in the tree was taken at postulo a6363a397, on 2026-10-01.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Any

SNAPSHOT = Path(__file__).with_name("openapi.json")


@cache
def description() -> dict:
    """The instance's OpenAPI description, as checked in."""
    return json.loads(SNAPSHOT.read_text(encoding="utf-8"))


@dataclass(frozen=True)
class Operation:
    """One method at one address, and what it is willing to be asked."""

    method: str
    template: str
    definition: dict
    document: dict

    @property
    def query(self) -> dict[str, dict]:
        return {
            parameter["name"]: parameter.get("schema", {})
            for parameter in self.definition.get("parameters", [])
            if parameter.get("in") == "query"
        }

    def check(self, params: dict[str, str]) -> None:
        """Refuse anything the instance would not recognise, naming it."""
        declared = self.query
        for name, value in params.items():
            assert name in declared, (
                f"{self.method} {self.template} does not take {name!r}. It takes "
                f"{sorted(declared) or 'no query parameters'}; sending anything else means "
                "the instance ignores it and answers something other than what was asked."
            )
            kind = declared[name].get("type") or _any_of(declared[name])
            if kind == "integer":
                assert re.fullmatch(r"-?\d+", value), (
                    f"{name!r} on {self.method} {self.template} is an integer; got {value!r}."
                )
            if kind == "boolean":
                assert value in ("true", "false"), (
                    f"{name!r} on {self.method} {self.template} is a boolean; got {value!r}."
                )

    @property
    def paginated(self) -> bool:
        """Whether the answer is the ``{items, count}`` envelope rather than a bare array."""
        schema = _resolve(self.document, self._success)
        return bool(schema) and {"items", "count"} <= set(schema.get("properties", {}))

    @property
    def status(self) -> int:
        """The status a success carries here: 201 where the API says so, 200 otherwise."""
        codes = sorted(
            code for code in self.definition.get("responses", {}) if code.startswith("2")
        )
        return int(codes[0]) if codes else 200

    @property
    def _success(self) -> dict:
        for code in sorted(self.definition.get("responses", {})):
            if code.startswith("2"):
                response = self.definition["responses"][code]
                return response.get("content", {}).get("application/json", {}).get("schema", {})
        return {}

    def answer(self, body: Any) -> Any:
        """What the instance would put on the wire for this body."""
        if self.paginated:
            rows = body if isinstance(body, list) else [body]
            return {"items": rows, "count": len(rows)}
        return body


def _any_of(schema: dict) -> str | None:
    for option in schema.get("anyOf", []):
        if option.get("type") not in (None, "null"):
            return option["type"]
    return None


def _resolve(document: dict, schema: dict) -> dict:
    reference = schema.get("$ref")
    if not reference:
        return schema
    node: Any = document
    for step in reference.removeprefix("#/").split("/"):
        node = node.get(step, {})
    return node


def operation(method: str, path: str) -> Operation:
    """The operation at this address, or an assertion saying the API has no such thing."""
    document = description()
    paths = document["paths"]
    template = path if path in paths else _match(paths, path)
    assert template, (
        f"The API has no {path}. Its addresses are {sorted(paths)[:5]} and the rest; a "
        "tool asking for one it does not have would get a 404 from a real instance."
    )
    definition = paths[template].get(method.lower())
    assert definition is not None, (
        f"{template} has no {method.upper()}: it has "
        f"{sorted(name.upper() for name in paths[template])}."
    )
    return Operation(
        method=method.upper(), template=template, definition=definition, document=document
    )


def _match(paths: dict, path: str) -> str | None:
    """The templated address this path belongs to — ``/applications/{pk}`` for one of them."""
    for template in paths:
        pattern = "[^/]+".join(re.escape(part) for part in re.split(r"\{[^}]+\}", template))
        if re.fullmatch(pattern, path):
            return template
    return None
