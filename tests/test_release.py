"""What makes a version nameable: the tag, the code and the changelog agree (#1)."""

import importlib.util
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("release_tools", ROOT / "scripts/release_tools.py")
tools = importlib.util.module_from_spec(_spec)
sys.modules["release_tools"] = tools
_spec.loader.exec_module(tools)


def project(tmp_path, version, changelog):
    (tmp_path / "pyproject.toml").write_text(f'[project]\nname = "x"\nversion = "{version}"\n')
    (tmp_path / "CHANGELOG.md").write_text(changelog)
    return tmp_path


LOG = (
    "# Changelog\n\n## [Unreleased]\n\n- later\n\n## [1.2.3] - 2026-01-01\n\n- a\n- b\n\n"
    "## [1.2.2]\n\n- old\n"
)


def test_a_tag_that_agrees_passes_and_its_notes_are_its_own_section(tmp_path):
    root = project(tmp_path, "1.2.3", LOG)
    assert tools.check("v1.2.3", root) == "1.2.3"
    assert tools.changelog_section("1.2.3", root) == "- a\n- b\n"


def test_a_tag_that_disagrees_with_the_code_is_refused(tmp_path):
    root = project(tmp_path, "1.2.4", LOG)
    with pytest.raises(tools.ReleaseError, match="pyproject.toml says 1.2.4"):
        tools.check("v1.2.3", root)


def test_a_tag_with_no_changelog_section_is_refused(tmp_path):
    root = project(tmp_path, "1.2.5", LOG)
    with pytest.raises(tools.ReleaseError, match=r"no '## \[1.2.5\]'"):
        tools.check("v1.2.5", root)


@pytest.mark.parametrize("tag", ["1.2.3", "v1.2", "v1.2.3+x", "main"])
def test_only_a_release_tag_is_a_tag(tag):
    with pytest.raises(tools.ReleaseError):
        tools.version_of_tag(tag)


def test_a_token_is_never_sent_over_plain_http():
    with pytest.raises(tools.ReleaseError, match="https"):
        tools._api("GET", "http://forge.example/api/v1", "secret")


def test_this_repository_is_releasable_as_it_stands():
    version = tools.pyproject_version(ROOT)
    assert tools.check(f"v{version}", ROOT) == version


def test_the_readme_installs_the_version_the_code_is():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    pinned = re.findall(r"postulo-mcp\.git@v(\d+\.\d+\.\d+)\n", readme)
    assert pinned == [tools.pyproject_version(ROOT)]


def test_a_security_policy_and_a_weekly_audit_exist():
    assert "privately" in (ROOT / "SECURITY.md").read_text(encoding="utf-8")
    weekly = (ROOT / ".forgejo/workflows/weekly.yml").read_text(encoding="utf-8")
    assert "schedule:" in weekly and "pip-audit" in weekly
