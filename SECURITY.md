# Security policy

postulo-mcp sits between an AI agent and somebody's whole job search, so reports are taken
seriously. Please report a vulnerability privately rather than in a public issue: open a
confidential issue on the
[Forgejo repository](https://source.tiagoagueda.com/postulo/postulo-mcp), or write to
<tiago.agueda@tiagoagueda.com>, which needs no account anywhere. Include the version, what
an attacker could achieve and how to reproduce it. You can expect an answer within a week.

A problem in Postulo itself goes to [Postulo's policy](https://source.tiagoagueda.com/postulo/postulo/src/branch/main/SECURITY.md).

## What this server promises

These are tested (`tests/test_server.py`), not only written down:

- **Read-only unless told otherwise.** Without `--write` the write tools are not registered
  at all, and writing needs a token that carries the `write` scope as well.
- **Nothing destructive.** No tool deletes, and no file is returned as bytes.
- **Somebody else's words are fenced** before a model sees them, and what an instance
  answered when a call failed is fenced too.
- **The token never reaches a command line.** The address and token come from the
  environment.
- **A port is never open without a secret**, is bounded, throttled and checks `Host` and
  `Origin`.

What it cannot do: make a model obey a fence, or decide what you let an agent read. A
`read` token exposes the whole job search to the model you chose; use one token per agent
and revoke it in Postulo when you stop using the agent.

## Supported versions

The latest release of the 0.x line that pairs with the Postulo you run. Dependencies are
audited on every push and every week.
