"""postulo-mcp: an agent's way into a Postulo instance, over the Model Context Protocol.

Postulo's plan says AI assistance is not in the application: it is complete and useful
without an API key, and a plugin can add it later against the same contracts. This is
that sentence the other way round. **No model runs inside Postulo and no API key is
stored there.** The agent lives wherever the person already runs one — Claude Desktop,
Claude Code, an editor, something self-hosted — and this server answers it through
Postulo's ordinary API, with a personal access token whose scopes the person chose.

Someone who wants nothing to do with agents installs nothing and nothing changes. Someone
who does gets an agent that knows their job search without it being pasted into a chat
window, and every write it makes is on the timeline with the token's name against it.
"""

from importlib.metadata import PackageNotFoundError, version

try:
    #: Read from the installed distribution rather than written here, so that the number an
    #: agent is told — it goes out in the MCP handshake and in every User-Agent — is the
    #: number of the package somebody actually installed. Two places to write it is one
    #: place to forget: this said 0.1.0 for the whole of 0.3.0's development (#3).
    __version__ = version("postulo-mcp")
except PackageNotFoundError:  # pragma: no cover - only when run from a source tree
    __version__ = "0.0.0"
