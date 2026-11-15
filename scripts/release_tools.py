"""What a release needs, in one file the workflow and a person can both run.

    python scripts/release_tools.py check v0.5.1             # tag, version and changelog agree
    python scripts/release_tools.py notes 0.5.1 -o notes.md  # that version's changelog section
    python scripts/release_tools.py publish v0.5.1 dist/*    # a Forgejo release with those files

The same shape as the core's scripts/release_tools.py, cut down to what a package that is
not installed into Postulo needs. Standard library only. ``publish`` talks to the Forgejo
API with ``FORGEJO_TOKEN``; the server and repository come from ``FORGEJO_URL`` and
``FORGEJO_REPOSITORY`` (``owner/name``), which Forgejo Actions set.
"""

from __future__ import annotations

import argparse
import json
import mimetypes
import os
import re
import sys
import tomllib
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


class ReleaseError(Exception):
    pass


def version_of_tag(tag: str) -> str:
    if not re.fullmatch(r"v\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?", tag):
        raise ReleaseError(f"{tag!r} is not a release tag; expected vX.Y.Z or vX.Y.Z-rc.1.")
    return tag[1:]


def pyproject_version(root: Path = ROOT) -> str:
    data = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    return data["project"]["version"]


def changelog_section(version: str, root: Path = ROOT) -> str:
    """The body of ``## [version] - date`` in CHANGELOG.md, without the heading."""
    text = (root / "CHANGELOG.md").read_text(encoding="utf-8")
    heading = re.compile(
        rf"^## \[{re.escape(version)}\](?:\s*[—-]\s*\d{{4}}-\d{{2}}-\d{{2}})?\s*$", re.MULTILINE
    )
    match = heading.search(text)
    if not match:
        raise ReleaseError(
            f"CHANGELOG.md has no '## [{version}]' section. Write it before tagging."
        )
    rest = text[match.end() :]
    following = re.search(r"^## ", rest, re.MULTILINE)
    body = (rest[: following.start()] if following else rest).strip()
    if not body:
        raise ReleaseError(f"The CHANGELOG.md section for {version} is empty.")
    return body + "\n"


def check(tag: str, root: Path = ROOT) -> str:
    """Refuse a tag that disagrees with pyproject.toml or the changelog; the version."""
    version = version_of_tag(tag)
    declared = pyproject_version(root)
    if declared != version:
        raise ReleaseError(f"The tag says {version} but pyproject.toml says {declared}.")
    changelog_section(version, root)
    return version


def _api(
    method: str, url: str, token: str, body: bytes | None = None, kind: str = "application/json"
):
    if not url.startswith("https://"):
        raise ReleaseError(f"Refusing to send a token to {url!r}: it is not an https address.")
    request = urllib.request.Request(url, data=body, method=method)  # noqa: S310 - https only
    request.add_header("Authorization", f"token {token}")
    request.add_header("Accept", "application/json")
    if body is not None:
        request.add_header("Content-Type", kind)
    try:
        with urllib.request.urlopen(request, timeout=60) as response:  # noqa: S310
            raw = response.read()
            return json.loads(raw) if raw else None
    except urllib.error.HTTPError as error:
        detail = error.read().decode(errors="replace")[:300]
        raise ReleaseError(f"{method} {url} -> {error.code}: {detail}") from error


def publish(
    tag: str, assets: list[Path], *, server: str, repository: str, token: str, notes: str
) -> dict:
    """Create the release for ``tag`` (or find it) and attach the files it lacks."""
    version = version_of_tag(tag)
    base = f"{server.rstrip('/')}/api/v1/repos/{repository}"
    try:
        release = _api("GET", f"{base}/releases/tags/{urllib.parse.quote(tag)}", token)
    except ReleaseError as error:
        if "404" not in str(error):
            raise
        release = None
    if release is None:
        payload = json.dumps(
            {
                "tag_name": tag,
                "name": f"postulo-mcp {version}",
                "body": notes,
                "draft": False,
                "prerelease": "-" in version,
            }
        ).encode("utf-8")
        release = _api("POST", f"{base}/releases", token, payload)
    already = {asset["name"] for asset in release.get("assets") or []}
    for path in assets:
        if path.name in already:
            continue
        boundary = uuid.uuid4().hex
        kind = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        body = (
            (
                f'--{boundary}\r\nContent-Disposition: form-data; name="attachment"; '
                f'filename="{path.name}"\r\nContent-Type: {kind}\r\n\r\n'
            ).encode()
            + path.read_bytes()
            + f"\r\n--{boundary}--\r\n".encode()
        )
        _api(
            "POST",
            f"{base}/releases/{release['id']}/assets?name={urllib.parse.quote(path.name)}",
            token,
            body,
            f"multipart/form-data; boundary={boundary}",
        )
    return release


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    commands = parser.add_subparsers(dest="command", required=True)
    one = commands.add_parser("check")
    one.add_argument("tag")
    two = commands.add_parser("notes")
    two.add_argument("version")
    two.add_argument("-o", "--output")
    three = commands.add_parser("publish")
    three.add_argument("tag")
    three.add_argument("files", nargs="+", type=Path)
    three.add_argument("--notes", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == "check":
            check(args.tag)
            print(f"{args.tag}: pyproject.toml and CHANGELOG.md agree.")
        elif args.command == "notes":
            text = changelog_section(args.version)
            if args.output:
                Path(args.output).write_text(text, encoding="utf-8")
            else:
                sys.stdout.write(text)
        else:
            release = publish(
                args.tag,
                list(args.files),
                server=os.environ["FORGEJO_URL"],
                repository=os.environ["FORGEJO_REPOSITORY"],
                token=os.environ["FORGEJO_TOKEN"],
                notes=args.notes.read_text(encoding="utf-8"),
            )
            print(f"Release {release.get('html_url', args.tag)}")
    except (ReleaseError, KeyError) as error:
        print(f"release_tools: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
