# postulo-mcp

**Let an AI agent read — and, if you allow it, record — your job search, through
[Postulo](https://source.tiagoagueda.com/postulo/postulo)'s own API, over the Model
Context Protocol.**

Postulo has no AI in it and never asks you for an API key. This is the other side of that
promise: the agent lives wherever you already run one, and this small server answers it
using a personal access token whose scopes you chose. Want nothing to do with agents?
Install nothing; nothing changes.

## Install

Anywhere your agent can run a command — the same machine as the agent, not necessarily
the same machine as Postulo:

```sh
uv tool install git+https://source.tiagoagueda.com/postulo/postulo-mcp.git
```

## Make a token

In Postulo, **Settings → API tokens → New token**. Tick **Read everything**, and nothing
else if you only want the agent to look. The token is shown once.

## Point an agent at it

Claude Desktop (`claude_desktop_config.json`) or any client that speaks stdio:

```json
{
  "mcpServers": {
    "postulo": {
      "command": "postulo-mcp",
      "env": {
        "POSTULO_URL": "https://postulo.example.org",
        "POSTULO_TOKEN": "the token you just made"
      }
    }
  }
}
```

Claude Code:

```sh
claude mcp add postulo --env POSTULO_URL=https://postulo.example.org \
  --env POSTULO_TOKEN=… -- postulo-mcp
```

The address and the token come from the environment on purpose, so neither ever shows up
in a command line or a process list.

## What the agent can do

**Read** (a `read` token): `list_applications`, `get_application` with its whole timeline,
`search_postings`, `search`, `list_reminders`, `list_companies`, `get_contacts`,
`list_interviews`, `get_cv`, `get_cover_letter`, `get_insights`. An application, a CV or a
letter is also addressable as a resource — `postulo://applications/7` — so an agent can
read one without spending a tool call.

**Write**, only with `--write` *and* a token carrying the `write` scope: `add_note`,
`change_status`, `add_reminder`, `complete_reminder`, `create_cover_letter_draft`.

```sh
postulo-mcp --write
```

**Nothing destructive, ever.** There is no delete tool, anywhere, and there will not be
one. Files are never returned as bytes: a CV travels as text.

Two prompts come with it: *prepare for interview*, which gathers the posting, the company,
the people and the timeline in one go, and *weekly review*, which asks what is due, what
went quiet and what is ahead.

## What to know before you turn writing on

Every write lands on the application's timeline with the token's name against it, so you
can always see what the agent did and undo it by hand. That record is what makes letting
an agent write acceptable; it is not a licence for it to write without telling you, and
the server's instructions say so to the model in as many words.

A `read` token exposes to the model you chose: your applications and their timelines, the
companies and people you recorded, your CVs and letters as text, your reminders,
interviews and figures. That is your whole job search. Decide that deliberately, and use a
token you can revoke.

## Develop

```sh
uv sync
uv run pytest
uv run ruff check .
```

The tests run every tool against a Postulo API that lives in memory. No request leaves the
machine, and no Postulo installation is needed.

## Licence

AGPL-3.0-or-later, like Postulo.
