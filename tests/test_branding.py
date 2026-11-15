"""The logo is Postulo's own, unmodified, and the Model Context Protocol's is not here (#2)."""

import hashlib
import re
from importlib import resources
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
NOTICE = (ROOT / "NOTICE").read_text(encoding="utf-8")
PNG = resources.files("postulo_mcp") / "assets" / "postulo-logo-256.png"


def listed(name: str) -> str:
    """The sha256 NOTICE records for a file: the first one after its path."""
    found = re.search(re.escape(name) + r"[\s\S]*?sha256\s+([0-9a-f]{64})", NOTICE)
    assert found, f"NOTICE does not record a checksum for {name}"
    return found.group(1)


def test_the_logo_is_the_one_notice_describes():
    svg = (ROOT / "assets" / "postulo-logo.svg").read_bytes()
    assert hashlib.sha256(svg).hexdigest() == listed("assets/postulo-logo.svg")
    assert hashlib.sha256(PNG.read_bytes()).hexdigest() == listed("postulo-logo-256.png")


def test_the_packaged_logo_is_a_square_png():
    data = PNG.read_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    assert int.from_bytes(data[16:20], "big") == int.from_bytes(data[20:24], "big") == 256
    assert len(data) < 2 * 1024 * 1024  # the ceiling Postulo puts on a plugin's logo


def test_the_svg_carries_no_script_or_outside_reference():
    svg = (ROOT / "assets" / "postulo-logo.svg").read_text(encoding="utf-8").lower()
    assert "<script" not in svg
    assert not re.search(r"(?:href|src)\s*=\s*[\"'](?!#|data:)", svg)


def test_the_readme_shows_postulos_logo_and_nobody_elses():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert readme.startswith('<img src="assets/postulo-logo.svg"')
    assert "modelcontextprotocol.io/images" not in readme
    assert not list(ROOT.glob("assets/*mcp*")) and not list(ROOT.glob("assets/*model-context*"))
