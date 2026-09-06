"""The server, against a Postulo API that lives in memory."""

from __future__ import annotations

import json

import httpx
import pytest

from postulo_mcp import __main__
from postulo_mcp import server as server_module
from postulo_mcp.client import Postulo, PostuloError, Settings

TOKEN = "pk_live_abcdef"  # noqa: S105 - a fixture, not a credential

APPLICATION = {
    "id": 7,
    "status": "applied",
    "posting": {"title": "Research Engineer", "company": {"name": "Black Mesa"}},
    "events": [{"kind": "note", "summary": "Applied through their site", "body": ""}],
}


class FakeApi:
    """Enough of Postulo's API to answer every tool, and to refuse a bad token."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict, dict]] = []
        self.status = 200
        self.body: object | None = None

    def handler(self, request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content) if request.content else {}
        self.calls.append((request.method, request.url.path, dict(request.url.params), payload))
        if request.headers.get("Authorization") != f"Bearer {TOKEN}":
            return httpx.Response(401, json={"detail": "No such token."})
        if self.body is not None or self.status != 200:
            return httpx.Response(self.status, json=self.body)

        path = request.url.path.removeprefix("/api/v1")
        if path == "/applications":
            return httpx.Response(200, json=[APPLICATION])
        if path == "/applications/7":
            return httpx.Response(200, json=APPLICATION)
        if path.endswith("/events"):
            return httpx.Response(201, json={"id": 3, "kind": "note", **payload})
        if path.endswith("/status"):
            return httpx.Response(200, json={**APPLICATION, "status": payload["status"]})
        if path == "/reminders":
            if request.method == "POST":
                return httpx.Response(201, json={"id": 9, **payload})
            return httpx.Response(200, json=[{"id": 9, "summary": "Chase Black Mesa"}])
        if path == "/reminders/9/complete":
            return httpx.Response(200, json={"id": 9, "done": True})
        if path == "/letters":
            if request.method == "POST":
                return httpx.Response(201, json={"id": 4, **payload})
            return httpx.Response(200, json=[{"id": 4, "name": "General"}])
        if path == "/letters/4":
            return httpx.Response(200, json={"id": 4, "name": "General", "body": "Dear …"})
        if path == "/cvs":
            return httpx.Response(200, json=[{"id": 2, "name": "Backend EN"}])
        if path == "/cvs/2":
            return httpx.Response(200, json={"id": 2, "name": "Backend EN", "headline": "Backend"})
        if path == "/insights":
            return httpx.Response(200, json={"applications": 12, "response_rate": 0.25})
        if path == "/search":
            return httpx.Response(200, json=[{"group": "Applications", "items": [APPLICATION]}])
        if path in ("/listings", "/companies", "/interviews"):
            return httpx.Response(200, json=[{"id": 1}])
        if path == "/companies/1":
            return httpx.Response(200, json={"id": 1, "name": "Black Mesa", "contacts": []})
        return httpx.Response(404, json={"detail": "Nothing there."})


@pytest.fixture
def api():
    return FakeApi()


def a_postulo(api: FakeApi, *, read_only=True, token=TOKEN) -> Postulo:
    return Postulo(
        Settings(url="https://postulo.example", token=token, read_only=read_only),
        transport=httpx.MockTransport(api.handler),
    )


def tools_of(server) -> dict:
    return {tool.name: tool for tool in server._tool_manager.list_tools()}


async def call(server, tool, /, **arguments):  # positional-only: a tool takes a "name" too
    """Call a tool and hand back what the agent would see, already parsed."""
    result = await server.call_tool(tool, arguments)
    assert not result.is_error, result.content
    return json.loads(result.content[0].text)


# ------------------------------------------------------------------ settings


def test_the_environment_says_where_and_how(monkeypatch):
    monkeypatch.delenv("POSTULO_URL", raising=False)
    monkeypatch.delenv("POSTULO_TOKEN", raising=False)
    with pytest.raises(PostuloError, match="POSTULO_URL"):
        Settings.from_environment()

    monkeypatch.setenv("POSTULO_URL", "https://postulo.example/")
    with pytest.raises(PostuloError, match="POSTULO_TOKEN"):
        Settings.from_environment()

    monkeypatch.setenv("POSTULO_TOKEN", TOKEN)
    settings = Settings.from_environment()
    assert settings.url == "https://postulo.example" and settings.read_only is True

    monkeypatch.setenv("POSTULO_MCP_READ_ONLY", "false")
    assert Settings.from_environment().read_only is False
    assert Settings.from_environment(read_only=True).read_only is True


# ------------------------------------------------------------------- reading


async def test_reading_tools_ask_the_api_and_hand_back_what_it_said(api):
    server = server_module.build(a_postulo(api))
    application = await call(server, "get_application", application_id=7)
    assert application["posting"]["company"]["name"] == "Black Mesa"

    await call(server, "list_applications", status="applied", quiet=True)
    method, path, params, _payload = api.calls[-1]
    assert method == "GET" and path == "/api/v1/applications"
    assert params == {"status": "applied", "quiet": "true"}, "empty filters are left off"

    await call(server, "search", query="black mesa")
    assert api.calls[-1][2] == {"q": "black mesa"}

    await call(server, "get_cv")
    assert api.calls[-1][1] == "/api/v1/cvs"
    await call(server, "get_cv", cv_id=2)
    assert api.calls[-1][1] == "/api/v1/cvs/2"


async def test_every_reading_tool_is_offered_and_nothing_destructive_is(api):
    tools = tools_of(server_module.build(a_postulo(api)))
    for name in (
        "list_applications",
        "get_application",
        "search_postings",
        "search",
        "list_reminders",
        "list_companies",
        "get_contacts",
        "list_interviews",
        "get_cv",
        "get_cover_letter",
        "get_insights",
    ):
        assert name in tools, name
    assert not [name for name in tools if "delete" in name or "remove" in name]


# ------------------------------------------------------------------- writing


async def test_a_read_only_server_offers_no_write_tools(api):
    tools = tools_of(server_module.build(a_postulo(api)))
    for name in ("add_note", "change_status", "add_reminder", "complete_reminder"):
        assert name not in tools


async def test_a_read_only_client_refuses_a_write_in_words(api):
    postulo = a_postulo(api)
    with pytest.raises(PostuloError, match="read-only"):
        postulo.post("/applications/7/events", {"summary": "x"})
    assert not [call for call in api.calls if call[0] != "GET"]


async def test_with_write_on_the_tools_appear_and_go_through_the_api(api):
    server = server_module.build(a_postulo(api, read_only=False))
    tools = tools_of(server)
    assert {
        "add_note",
        "change_status",
        "add_reminder",
        "complete_reminder",
        "create_cover_letter_draft",
    } <= set(tools)

    await call(server, "add_note", application_id=7, summary="Called them", body="No answer")
    method, path, _params, payload = api.calls[-1]
    assert method == "POST" and path == "/api/v1/applications/7/events"
    assert payload == {"kind": "note", "summary": "Called them", "body": "No answer"}

    await call(server, "change_status", application_id=7, status="interviewing", note="Invited")
    assert api.calls[-1][3] == {"status": "interviewing", "note": "Invited"}

    await call(server, "add_reminder", summary="Chase", due_at="2026-09-20T09:00:00Z")
    assert api.calls[-1][3]["application_id"] is None

    await call(server, "complete_reminder", reminder_id=9)
    assert api.calls[-1][1] == "/api/v1/reminders/9/complete"

    await call(server, "create_cover_letter_draft", name="For Black Mesa", body="Dear …")
    assert api.calls[-1][3]["is_template"] is False


# ------------------------------------------------------------------- errors


async def test_a_refused_token_is_explained_rather_than_thrown(api):
    postulo = a_postulo(api, token="wrong")  # noqa: S106
    with pytest.raises(PostuloError, match="refused the token"):
        postulo.get("/applications")


async def test_other_failures_carry_what_the_instance_said(api):
    postulo = a_postulo(api)
    api.status, api.body = 422, {"detail": "status: not a status"}
    with pytest.raises(PostuloError, match="422: status: not a status"):
        postulo.get("/applications")
    api.status, api.body = 404, {"detail": "gone"}
    with pytest.raises(PostuloError, match="no such thing"):
        postulo.get("/applications/99")


async def test_an_unreachable_instance_says_so():
    def refuse(request):
        raise httpx.ConnectError("connection refused")

    postulo = Postulo(
        Settings(url="https://postulo.example", token=TOKEN),
        transport=httpx.MockTransport(refuse),
    )
    with pytest.raises(PostuloError, match="Could not reach Postulo"):
        postulo.get("/applications")


# ---------------------------------------------------------------- resources


async def test_resources_return_readable_text_not_json(api):
    server = server_module.build(a_postulo(api))
    contents = await server.read_resource("postulo://applications/7")
    text = list(contents)[0].content
    assert "status: applied" in text and "title: Research Engineer" in text
    assert "{" not in text, "a model reads prose better than braces"

    contents = await server.read_resource("postulo://cvs/2")
    assert "Backend EN" in list(contents)[0].content


# ------------------------------------------------------------------ prompts


async def test_the_prompts_tell_the_agent_where_to_look(api):
    server = server_module.build(a_postulo(api))
    names = {prompt.name for prompt in await server.list_prompts()}
    assert {"prepare_for_interview", "weekly_review"} <= names

    result = await server.get_prompt("prepare_for_interview", {"company": "Black Mesa"})
    text = result.messages[0].content.text
    assert "Black Mesa" in text and "get_application" in text
    assert "Do not invent" in text


# ------------------------------------------------------------- the front door


def test_the_command_line_defaults_to_read_only_over_stdio():
    assert __main__.parse([]).write is False
    assert __main__.parse([]).transport == "stdio"
    assert __main__.parse(["--write"]).write is True
    assert __main__.parse(["--transport", "streamable-http"]).transport == "streamable-http"


def test_it_stops_with_a_sentence_when_the_environment_is_empty(monkeypatch, capsys):
    monkeypatch.delenv("POSTULO_URL", raising=False)
    monkeypatch.delenv("POSTULO_TOKEN", raising=False)
    assert __main__.main([]) == 2
    assert "POSTULO_URL" in capsys.readouterr().err


def test_the_server_it_builds_is_named_and_instructed(api):
    server = server_module.build(a_postulo(api))
    assert server.name == "postulo"
    assert "never invent a fact" in server_module.INSTRUCTIONS.lower()
