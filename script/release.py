"""Release checks and release notes for FluentPet Connect.

Usage:
    python script/release.py check vX.Y.Z
    python script/release.py notes X.Y.Z [--output FILE]

``check`` verifies that a release tag matches the version in
``custom_components/fluentpet/manifest.json`` and ``pyproject.toml`` and that
``CHANGELOG.md`` has a non-empty section for that version. ``notes`` prints the
changelog section of a version, for use as the GitHub release notes.

A version section in ``CHANGELOG.md`` starts with a heading line that is
exactly ``## X.Y.Z`` and ends at the next ``## `` heading or the end of the
file. The script uses the standard library only.
"""

import argparse
from collections.abc import Sequence
from dataclasses import dataclass
import json
from pathlib import Path
import re
import sys
import tomllib

REPO_ROOT = Path(__file__).resolve().parent.parent

TAG_PATTERN = re.compile(r"v(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)")
VERSION_PATTERN = re.compile(r"(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)")


class ReleaseError(Exception):
    """A release check failed."""


@dataclass(frozen=True, slots=True)
class RepoPaths:
    """Files that hold the release version.

    Attributes:
        manifest: The integration ``manifest.json``.
        pyproject: The project ``pyproject.toml``.
        changelog: The ``CHANGELOG.md``.
    """

    manifest: Path
    pyproject: Path
    changelog: Path

    @classmethod
    def from_root(cls, root: Path) -> RepoPaths:
        """Return the paths of the release files below a repository root.

        Args:
            root: The repository root.

        Returns:
            The paths of the release files.
        """
        return cls(
            manifest=root / "custom_components" / "fluentpet" / "manifest.json",
            pyproject=root / "pyproject.toml",
            changelog=root / "CHANGELOG.md",
        )


def version_from_tag(tag: str) -> str:
    """Return the version of a release tag.

    Args:
        tag: A tag of the form ``vX.Y.Z``, without leading zeros or a
            prerelease or build suffix.

    Returns:
        The version ``X.Y.Z``.

    Raises:
        ReleaseError: The tag is not of the form ``vX.Y.Z``.
    """
    if TAG_PATTERN.fullmatch(tag) is None:
        raise ReleaseError(
            f"Tag {tag!r} is not of the form vX.Y.Z (numbers without leading"
            " zeros, no prerelease or build suffix)"
        )
    return tag[1:]


def validate_version(version: str) -> str:
    """Return a version after checking that it is of the form ``X.Y.Z``.

    Args:
        version: The version to check.

    Returns:
        The version, unchanged.

    Raises:
        ReleaseError: The version is not of the form ``X.Y.Z``.
    """
    if VERSION_PATTERN.fullmatch(version) is None:
        raise ReleaseError(
            f"Version {version!r} is not of the form X.Y.Z (numbers without"
            " leading zeros, no prerelease or build suffix)"
        )
    return version


def manifest_version(text: str) -> str:
    """Return the ``version`` of an integration manifest.

    Args:
        text: The content of ``manifest.json``.

    Returns:
        The manifest version.

    Raises:
        ReleaseError: The manifest is not valid JSON or has no string version.
    """
    try:
        data = json.loads(text)
    except json.JSONDecodeError as err:
        raise ReleaseError(f"manifest.json is not valid JSON: {err}") from err
    version = data.get("version") if isinstance(data, dict) else None
    if not isinstance(version, str):
        raise ReleaseError("manifest.json has no string 'version'")
    return version


def pyproject_version(text: str) -> str:
    """Return the ``[project] version`` of a ``pyproject.toml``.

    Args:
        text: The content of ``pyproject.toml``.

    Returns:
        The project version.

    Raises:
        ReleaseError: The file is not valid TOML or has no string version.
    """
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as err:
        raise ReleaseError(f"pyproject.toml is not valid TOML: {err}") from err
    project = data.get("project")
    version = project.get("version") if isinstance(project, dict) else None
    if not isinstance(version, str):
        raise ReleaseError("pyproject.toml has no string [project] version")
    return version


def changelog_section(text: str, version: str) -> str:
    """Return the body of the changelog section of a version.

    The section starts after the line ``## <version>`` and ends before the next
    line that starts with ``## `` or at the end of the text.

    Args:
        text: The content of ``CHANGELOG.md``.
        version: The version, ``X.Y.Z``.

    Returns:
        The section body without leading and trailing whitespace.

    Raises:
        ReleaseError: The changelog has no section for the version, has more
            than one, or the section is empty.
    """
    heading = f"## {version}"
    lines = text.splitlines()
    starts = [index for index, line in enumerate(lines) if line.rstrip() == heading]
    if not starts:
        raise ReleaseError(f"CHANGELOG.md has no '{heading}' section")
    if len(starts) > 1:
        raise ReleaseError(f"CHANGELOG.md has more than one '{heading}' section")
    body: list[str] = []
    for line in lines[starts[0] + 1 :]:
        if line.startswith("## "):
            break
        body.append(line)
    notes = "\n".join(body).strip()
    if not notes:
        raise ReleaseError(f"CHANGELOG.md section '{heading}' is empty")
    return notes


def check_release(tag: str, paths: RepoPaths) -> str:
    """Check that the repository is ready to release a tag.

    Args:
        tag: The release tag, ``vX.Y.Z``.
        paths: The files that hold the release version.

    Returns:
        The version ``X.Y.Z`` of the tag.

    Raises:
        ReleaseError: A check failed. The message lists every failed check.
    """
    version = version_from_tag(tag)
    errors: list[str] = []
    found = manifest_version(paths.manifest.read_text(encoding="utf-8"))
    if found != version:
        errors.append(
            f"manifest.json version is {found!r}, expected {version!r} for tag {tag}"
        )
    found = pyproject_version(paths.pyproject.read_text(encoding="utf-8"))
    if found != version:
        errors.append(
            f"pyproject.toml [project] version is {found!r}, expected {version!r}"
            f" for tag {tag}"
        )
    try:
        changelog_section(paths.changelog.read_text(encoding="utf-8"), version)
    except ReleaseError as err:
        errors.append(str(err))
    if errors:
        raise ReleaseError("\n".join(errors))
    return version


def build_parser() -> argparse.ArgumentParser:
    """Return the command line parser.

    Returns:
        The parser.
    """
    parser = argparse.ArgumentParser(
        prog="release.py",
        description="Release checks and release notes for FluentPet Connect.",
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=REPO_ROOT,
        help="repository root (default: the repository of this script)",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    check = commands.add_parser(
        "check", help="check that the repository is ready to release a tag"
    )
    check.add_argument("tag", help="release tag, vX.Y.Z")
    notes = commands.add_parser(
        "notes", help="print the changelog section of a version"
    )
    notes.add_argument("version", help="version, X.Y.Z")
    notes.add_argument(
        "--output", type=Path, help="write the notes to this file instead"
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the command line interface.

    Args:
        argv: The arguments, without the program name. Defaults to
            ``sys.argv[1:]``.

    Returns:
        The exit status: 0 on success, 1 when a check fails.
    """
    args = build_parser().parse_args(argv)
    paths = RepoPaths.from_root(args.root)
    try:
        if args.command == "check":
            version = check_release(args.tag, paths)
            sys.stdout.write(f"{args.tag} is ready to release as {version}\n")
        else:
            version = validate_version(args.version)
            notes = changelog_section(
                paths.changelog.read_text(encoding="utf-8"), version
            )
            if args.output is None:
                sys.stdout.write(f"{notes}\n")
            else:
                args.output.write_text(f"{notes}\n", encoding="utf-8")
    except (ReleaseError, OSError) as err:
        sys.stderr.writelines(f"error: {line}\n" for line in str(err).splitlines())
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
