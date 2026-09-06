"""The tools, resources and prompts an agent sees, each one a call to Postulo's API.

Named as an agent's vocabulary rather than as endpoints, because the name is the whole
documentation a model reads before it decides. Nothing here is destructive: there is no
delete tool anywhere, and there never will be. Writing needs both a token with the
``write`` scope and a server started with ``--write``; either one missing and the write
tools refuse in a sentence rather than failing obscurely.
"""

from __future__ import annotations

from typing import Any

from mcp.server.mcpserver import MCPServer

from . import __version__
from .client import Postulo, PostuloError

INSTRUCTIONS = """
Postulo is this person's own job-application record: the listings they are considering,
the applications they have made, the timeline of what happened on each, the companies and
people involved, their CVs and letters, their reminders and interviews.

Read freely. Before writing anything, say what you are about to record and let them agree
— every write appears on their timeline attributed to this token, which is a record for
them, not a licence for you. Never invent a fact about an employer: if you do not know
whether something happened, say so and ask.
""".strip()


def build(postulo: Postulo) -> MCPServer:
    """An MCP server over one instance."""
    server = MCPServer(
        name="postulo",
        title="Postulo",
        version=__version__,
        instructions=INSTRUCTIONS,
        website_url="https://source.tiagoagueda.com/postulo/postulo",
    )
    writable = not postulo.settings.read_only

    # ------------------------------------------------------------- reading

    @server.tool(description="List the person's applications, most recent first.")
    def list_applications(
        status: str = "",
        company_id: int | None = None,
        since: str = "",
        open_only: bool = False,
        quiet: bool = False,
    ) -> Any:
        """`status` is one of draft, applied, acknowledged, screening, interviewing,
        assessment, offer, accepted, rejected, withdrawn, ghosted. `quiet` selects the
        ones an employer has stopped answering."""
        return postulo.get(
            "/applications",
            status=status,
            company=company_id,
            since=since,
            open_only=open_only or None,
            quiet=quiet or None,
        )

    @server.tool(description="One application in full, with its whole timeline.")
    def get_application(application_id: int) -> Any:
        return postulo.get(f"/applications/{application_id}")

    @server.tool(description="Search postings the person has saved but not yet applied to.")
    def search_postings(shortlisted: bool = False, undecided: bool = False) -> Any:
        return postulo.get(
            "/listings", shortlisted=shortlisted or None, undecided=undecided or None
        )

    @server.tool(description="Search everything the person has: applications, companies, notes.")
    def search(query: str) -> Any:
        return postulo.get("/search", q=query)

    @server.tool(description="Reminders. With due_only, just the ones that have fallen due.")
    def list_reminders(due_only: bool = True) -> Any:
        return postulo.get("/reminders", due=due_only or None)

    @server.tool(description="Companies the person has recorded.")
    def list_companies(query: str = "") -> Any:
        return postulo.get("/companies", q=query)

    @server.tool(description="One company, with the people recorded at it.")
    def get_contacts(company_id: int) -> Any:
        return postulo.get(f"/companies/{company_id}")

    @server.tool(description="Interviews. With upcoming, only those still ahead.")
    def list_interviews(upcoming: bool = True) -> Any:
        return postulo.get("/interviews", upcoming=upcoming or None)

    @server.tool(description="The person's CVs; give an id for one CV with everything on it.")
    def get_cv(cv_id: int | None = None) -> Any:
        return postulo.get(f"/cvs/{cv_id}") if cv_id else postulo.get("/cvs")

    @server.tool(description="The person's letters; give an id for one letter with its text.")
    def get_cover_letter(letter_id: int | None = None) -> Any:
        return postulo.get(f"/letters/{letter_id}") if letter_id else postulo.get("/letters")

    @server.tool(description="The figures: response rates, where applications come from, timings.")
    def get_insights() -> Any:
        return postulo.get("/insights")

    # ------------------------------------------------------------- writing

    if writable:

        @server.tool(description="Add a note to an application's timeline.")
        def add_note(application_id: int, summary: str, body: str = "") -> Any:
            return postulo.post(
                f"/applications/{application_id}/events",
                {"kind": "note", "summary": summary, "body": body},
            )

        @server.tool(description="Move an application to another status, with a reason.")
        def change_status(application_id: int, status: str, note: str = "") -> Any:
            return postulo.post(
                f"/applications/{application_id}/status", {"status": status, "note": note}
            )

        @server.tool(description="Add a reminder, optionally about one application.")
        def add_reminder(summary: str, due_at: str, application_id: int | None = None) -> Any:
            """`due_at` is an ISO 8601 moment, e.g. 2026-09-20T09:00:00Z."""
            return postulo.post(
                "/reminders",
                {"summary": summary, "due_at": due_at, "application_id": application_id},
            )

        @server.tool(description="Mark a reminder as done.")
        def complete_reminder(reminder_id: int) -> Any:
            return postulo.post(f"/reminders/{reminder_id}/complete")

        @server.tool(description="Save a draft cover letter for the person to edit and send.")
        def create_cover_letter_draft(
            name: str, body: str, subject: str = "", is_template: bool = False
        ) -> Any:
            return postulo.post(
                "/letters",
                {"name": name, "body": body, "subject": subject, "is_template": is_template},
            )

    # ----------------------------------------------------------- resources

    @server.resource(
        "postulo://applications/{application_id}",
        name="application",
        description="One application and its timeline, as text.",
        mime_type="text/plain",
    )
    def application_resource(application_id: str) -> str:
        return _as_text(postulo.get(f"/applications/{int(application_id)}"))

    @server.resource(
        "postulo://cvs/{cv_id}",
        name="cv",
        description="One CV, as text. The PDF is never returned.",
        mime_type="text/plain",
    )
    def cv_resource(cv_id: str) -> str:
        return _as_text(postulo.get(f"/cvs/{int(cv_id)}"))

    @server.resource(
        "postulo://letters/{letter_id}",
        name="cover letter",
        description="One cover letter, as text.",
        mime_type="text/plain",
    )
    def letter_resource(letter_id: str) -> str:
        return _as_text(postulo.get(f"/letters/{int(letter_id)}"))

    # ------------------------------------------------------------- prompts

    @server.prompt(description="Everything worth knowing before an interview at a company.")
    def prepare_for_interview(company: str) -> str:
        return (
            f"Help me prepare for my interview at {company}. Use search to find the "
            "application, then get_application for its timeline, get_contacts for the "
            "people, and get_cv for what I sent them. Tell me what they asked for, what I "
            "claimed, what has happened so far, and what I should expect to be asked. Do "
            "not invent anything about the company that is not in my records."
        )

    @server.prompt(description="A weekly review: what is waiting, what is due, what went quiet.")
    def weekly_review() -> str:
        return (
            "Give me my week: list_reminders for what is due, list_applications with "
            "quiet=true for the ones that have gone silent, list_interviews for what is "
            "ahead, and search_postings with undecided=true for what I have not decided "
            "about. Then tell me the three things worth doing first, and why."
        )

    return server


def _as_text(payload: Any, indent: int = 0) -> str:
    """A record as readable lines. A model reads prose better than it reads braces."""
    pad = " " * indent
    if isinstance(payload, dict):
        lines = []
        for key, value in payload.items():
            if value in (None, "", [], {}):
                continue
            if isinstance(value, dict | list):
                lines.append(f"{pad}{key}:")
                lines.append(_as_text(value, indent + 2))
            else:
                lines.append(f"{pad}{key}: {value}")
        return "\n".join(lines)
    if isinstance(payload, list):
        return "\n".join(_as_text(item, indent) for item in payload)
    return f"{pad}{payload}"


__all__ = ["INSTRUCTIONS", "PostuloError", "build"]
