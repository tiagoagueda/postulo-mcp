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

The snapshot in the tree was taken at postulo 42be52048, on 2026-10-19.
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
        """What the instance would put on the wire for this body.

        The fixture is held to the operation's success schema: a field the API does not
        send, or one it always sends and the fixture left out, fails the test here instead
        of letting a fence or a tool pass on a shape no instance produces (#10).
        """
        if self.paginated:
            rows = body if isinstance(body, list) else [body]
            body = {"items": rows, "count": len(rows)}
        problems = validate(self.document, self._success, body, "response")
        assert not problems, (
            f"The fixture for {self.method} {self.template} is not what the API sends: "
            + "; ".join(problems)
        )
        return body

    def check_body(self, payload: Any) -> None:
        """Refuse a request body the operation's request schema would not recognise."""
        content = self.definition.get("requestBody", {}).get("content", {})
        schema = content.get("application/json", {}).get("schema")
        if schema is None:
            assert not payload, (
                f"{self.method} {self.template} takes no body; got {sorted(payload)}."
            )
            return
        problems = validate(self.document, schema, payload, "request")
        assert not problems, (
            f"The body sent to {self.method} {self.template} is not what the API takes: "
            + "; ".join(problems)
        )


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


def validate(document: dict, schema: dict, value: Any, where: str = "value") -> list[str]:
    """What is wrong with ``value`` against ``schema``: a small check, not a full validator.

    It follows ``$ref``, ``anyOf``, ``properties``, ``required`` and ``items``, checks the
    basic types and the length and size limits, and — unlike a validator for incoming
    data — refuses a property the schema does not declare, because the point is to catch a
    fixture or a payload that has drifted from the API's description.
    """
    schema = _resolve(document, schema)
    options = schema.get("anyOf") or schema.get("oneOf")
    if options:
        attempts = [validate(document, option, value, where) for option in options]
        return [] if any(not attempt for attempt in attempts) else attempts[0]
    kind = schema.get("type")
    if kind == "null":
        return [] if value is None else [f"{where}: expected null"]
    if kind == "object" or "properties" in schema:
        if not isinstance(value, dict):
            return [f"{where}: expected an object, got {type(value).__name__}"]
        properties = schema.get("properties", {})
        if not properties and schema.get("additionalProperties", True):
            return []
        problems = [
            f"{where}.{name}: not in the schema" for name in value if name not in properties
        ]
        problems += [
            f"{where}.{name}: required and missing"
            for name in schema.get("required", [])
            if name not in value
        ]
        for name, item in value.items():
            if name in properties:
                problems += validate(document, properties[name], item, f"{where}.{name}")
        return problems
    if kind == "array":
        if not isinstance(value, list):
            return [f"{where}: expected a list, got {type(value).__name__}"]
        problems = []
        for index, item in enumerate(value):
            problems += validate(document, schema.get("items", {}), item, f"{where}[{index}]")
        return problems
    checks = {
        "string": lambda v: isinstance(v, str),
        "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
        "number": lambda v: isinstance(v, int | float) and not isinstance(v, bool),
        "boolean": lambda v: isinstance(v, bool),
    }
    if kind in checks and not checks[kind](value):
        return [f"{where}: expected {kind}, got {type(value).__name__}"]
    problems = []
    if kind == "string":
        if "maxLength" in schema and len(value) > schema["maxLength"]:
            problems.append(f"{where}: longer than {schema['maxLength']}")
        if "minLength" in schema and len(value) < schema["minLength"]:
            problems.append(f"{where}: shorter than {schema['minLength']}")
    if kind in ("integer", "number"):
        if "minimum" in schema and value < schema["minimum"]:
            problems.append(f"{where}: below {schema['minimum']}")
        if "maximum" in schema and value > schema["maximum"]:
            problems.append(f"{where}: above {schema['maximum']}")
    return problems


def sample(component: str, /, **fields: Any) -> dict:
    """A record in the shape of the component ``component``: what it must carry, then ``fields``.

    Required members get a placeholder of the right type, so a test spells out only the
    members it is about and the rest is as the API would send it.
    """
    document = description()
    return _fill(document, document["components"]["schemas"][component]) | fields


def _fill(document: dict, schema: dict) -> Any:
    schema = _resolve(document, schema)
    options = schema.get("anyOf") or schema.get("oneOf")
    if options:
        return _fill(document, options[0])
    kind = schema.get("type")
    if kind == "object" or "properties" in schema:
        properties = schema.get("properties", {})
        return {name: _fill(document, properties[name]) for name in schema.get("required", [])}
    return (
        {
            "string": "x",
            "integer": 1,
            "number": 1,
            "boolean": False,
            "array": [],
            "null": None,
        }.get(kind, None)
        if kind != "string"
        else _string(schema)
    )


def _string(schema: dict) -> str:
    return {"date-time": "2026-01-01T00:00:00Z", "date": "2026-01-01"}.get(
        schema.get("format", ""), "x"
    )
