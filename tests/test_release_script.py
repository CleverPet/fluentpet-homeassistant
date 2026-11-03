"""Tests for the release script."""

import json
from pathlib import Path

import pytest

from script.release import (
    ReleaseError,
    RepoPaths,
    changelog_section,
    check_release,
    main,
    version_from_tag,
)

CHANGELOG = """# Changelog

All notable changes to this project are documented in this file.

## Unreleased

- Upcoming change.

## 1.2.3

- Added a sensor.
- Fixed a crash.

### Notes

A subheading stays in the section.

## 1.2.2

- Older change.
"""

PYPROJECT = """[project]
name = "fluentpet-homeassistant"
version = "{version}"

[tool.other]
version = "9.9.9"
"""


def write_repo(
    root: Path,
    *,
    manifest_version: str = "1.2.3",
    pyproject_version: str = "1.2.3",
    changelog: str = CHANGELOG,
) -> RepoPaths:
    """Write the release files below a repository root and return their paths."""
    paths = RepoPaths.from_root(root)
    paths.manifest.parent.mkdir(parents=True)
    paths.manifest.write_text(
        json.dumps({"domain": "fluentpet", "version": manifest_version}),
        encoding="utf-8",
    )
    paths.pyproject.write_text(
        PYPROJECT.format(version=pyproject_version), encoding="utf-8"
    )
    paths.changelog.write_text(changelog, encoding="utf-8")
    return paths


@pytest.mark.parametrize(
    ("tag", "version"),
    [("v1.2.3", "1.2.3"), ("v0.0.1", "0.0.1"), ("v10.0.0", "10.0.0")],
)
def test_version_from_valid_tag(tag: str, version: str) -> None:
    """A vX.Y.Z tag gives its version."""
    assert version_from_tag(tag) == version


@pytest.mark.parametrize(
    "tag",
    [
        "v1.2",
        "1.2.3",
        "v01.2.3",
        "v1.02.3",
        "v1.2.03",
        "v1.2.3-rc1",
        "v1.2.3+build",
        "v1.2.3.4",
        "V1.2.3",
        " v1.2.3",
        "v1.2.3\n",
        "",
    ],
)
def test_malformed_tag(tag: str) -> None:
    """A tag that is not vX.Y.Z is refused."""
    with pytest.raises(ReleaseError, match="not of the form vX.Y.Z"):
        version_from_tag(tag)


def test_check_release_ok(tmp_path: Path) -> None:
    """Matching versions and a changelog section pass the check."""
    paths = write_repo(tmp_path)
    assert check_release("v1.2.3", paths) == "1.2.3"


def test_check_release_malformed_tag(tmp_path: Path) -> None:
    """The check refuses a malformed tag before reading any file."""
    paths = write_repo(tmp_path)
    with pytest.raises(ReleaseError, match="not of the form vX.Y.Z"):
        check_release("v1.2.3-rc1", paths)


def test_check_release_manifest_mismatch(tmp_path: Path) -> None:
    """A manifest version that differs from the tag fails the check."""
    paths = write_repo(tmp_path, manifest_version="1.2.2")
    with pytest.raises(ReleaseError, match=r"manifest\.json version is '1\.2\.2'"):
        check_release("v1.2.3", paths)


def test_check_release_manifest_without_version(tmp_path: Path) -> None:
    """A manifest without a version fails the check."""
    paths = write_repo(tmp_path)
    paths.manifest.write_text(json.dumps({"domain": "fluentpet"}), encoding="utf-8")
    with pytest.raises(ReleaseError, match="has no string 'version'"):
        check_release("v1.2.3", paths)


def test_check_release_pyproject_mismatch(tmp_path: Path) -> None:
    """A pyproject version that differs from the tag fails the check."""
    paths = write_repo(tmp_path, pyproject_version="1.2.4")
    with pytest.raises(
        ReleaseError, match=r"pyproject\.toml \[project\] version is '1\.2\.4'"
    ):
        check_release("v1.2.3", paths)


def test_check_release_reports_every_failure(tmp_path: Path) -> None:
    """The check lists every failed check, not only the first."""
    paths = write_repo(
        tmp_path,
        manifest_version="0.0.1",
        pyproject_version="0.0.1",
        changelog="# Changelog\n\n## Unreleased\n\n- Change.\n",
    )
    with pytest.raises(ReleaseError) as err:
        check_release("v1.2.3", paths)
    message = str(err.value)
    assert "manifest.json" in message
    assert "pyproject.toml" in message
    assert "no '## 1.2.3' section" in message


def test_check_release_missing_changelog_section(tmp_path: Path) -> None:
    """A version without a changelog section fails the check."""
    paths = write_repo(tmp_path, manifest_version="1.3.0", pyproject_version="1.3.0")
    with pytest.raises(ReleaseError, match="no '## 1.3.0' section"):
        check_release("v1.3.0", paths)


def test_check_release_empty_changelog_section(tmp_path: Path) -> None:
    """A changelog section without entries fails the check."""
    paths = write_repo(
        tmp_path, changelog="# Changelog\n\n## 1.2.3\n\n   \n\n## 1.2.2\n\n- Old.\n"
    )
    with pytest.raises(ReleaseError, match="section '## 1.2.3' is empty"):
        check_release("v1.2.3", paths)


def test_changelog_section_stops_at_next_heading() -> None:
    """The notes run from the version heading to the next version heading."""
    assert changelog_section(CHANGELOG, "1.2.3") == (
        "- Added a sensor.\n- Fixed a crash.\n\n### Notes\n\n"
        "A subheading stays in the section."
    )


def test_changelog_section_last_section() -> None:
    """The last section runs to the end of the file."""
    assert changelog_section(CHANGELOG, "1.2.2") == "- Older change."


def test_changelog_section_does_not_match_unreleased() -> None:
    """The Unreleased section never counts as a version section."""
    changelog = "# Changelog\n\n## Unreleased\n\n- 1.2.3 is coming.\n"
    with pytest.raises(ReleaseError, match="no '## 1.2.3' section"):
        changelog_section(changelog, "1.2.3")


@pytest.mark.parametrize(
    "heading", ["## 1.2.30", "## 1.2.3 - 2026-11-02", "### 1.2.3", "## v1.2.3"]
)
def test_changelog_section_needs_exact_heading(heading: str) -> None:
    """Only a heading line that is exactly '## X.Y.Z' starts the section."""
    with pytest.raises(ReleaseError, match="no '## 1.2.3' section"):
        changelog_section(f"# Changelog\n\n{heading}\n\n- Change.\n", "1.2.3")


def test_changelog_section_duplicate_heading() -> None:
    """Two sections for the same version are refused."""
    changelog = "## 1.2.3\n\n- One.\n\n## 1.2.3\n\n- Two.\n"
    with pytest.raises(ReleaseError, match="more than one"):
        changelog_section(changelog, "1.2.3")


def test_main_check(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """The check command exits 0 when the repository is ready."""
    write_repo(tmp_path)
    assert main(["--root", str(tmp_path), "check", "v1.2.3"]) == 0
    assert "ready to release as 1.2.3" in capsys.readouterr().out


def test_main_check_fails(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """The check command exits 1 and explains a failure on stderr."""
    write_repo(tmp_path, manifest_version="1.2.2")
    assert main(["--root", str(tmp_path), "check", "v1.2.3"]) == 1
    assert "error: manifest.json version is '1.2.2'" in capsys.readouterr().err


def test_main_notes_stdout(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """The notes command prints the section."""
    write_repo(tmp_path)
    assert main(["--root", str(tmp_path), "notes", "1.2.2"]) == 0
    assert capsys.readouterr().out == "- Older change.\n"


def test_main_notes_output_file(tmp_path: Path) -> None:
    """The notes command writes the section to the output file."""
    write_repo(tmp_path)
    output = tmp_path / "notes.md"
    assert (
        main(["--root", str(tmp_path), "notes", "1.2.2", "--output", str(output)]) == 0
    )
    assert output.read_text(encoding="utf-8") == "- Older change.\n"


@pytest.mark.parametrize("version", ["Unreleased", "v1.2.3", "1.2"])
def test_main_notes_invalid_version(
    tmp_path: Path, version: str, capsys: pytest.CaptureFixture[str]
) -> None:
    """The notes command refuses a version that is not X.Y.Z."""
    write_repo(tmp_path)
    assert main(["--root", str(tmp_path), "notes", version]) == 1
    assert "is not of the form X.Y.Z" in capsys.readouterr().err


def test_main_notes_missing_section(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The notes command fails for a version without a section."""
    write_repo(tmp_path)
    assert main(["--root", str(tmp_path), "notes", "9.9.9"]) == 1
    assert "no '## 9.9.9' section" in capsys.readouterr().err


def test_main_missing_file(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """A missing release file fails with an error instead of a traceback."""
    assert main(["--root", str(tmp_path), "check", "v1.2.3"]) == 1
    assert "error:" in capsys.readouterr().err
