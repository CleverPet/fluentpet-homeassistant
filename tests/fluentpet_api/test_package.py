"""Tests for the FluentPet API package boundaries."""

import ast
from pathlib import Path

from custom_components.fluentpet import fluentpet_api

PACKAGE_DIR = Path(fluentpet_api.__file__).parent


def _imported_modules(path: Path) -> list[str]:
    """Return every module a source file imports, relative imports as written."""
    modules: list[str] = []
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            modules.append("." * node.level + (node.module or ""))
    return modules


def test_no_homeassistant_imports() -> None:
    """The library never imports Home Assistant, so it can be extracted as is."""
    sources = sorted(PACKAGE_DIR.rglob("*.py"))
    assert len(sources) >= 7
    for path in sources:
        for module in _imported_modules(path):
            assert module.split(".")[0] != "homeassistant", f"{path.name}: {module}"
            assert "homeassistant" not in module, f"{path.name}: {module}"


def test_only_relative_or_allowed_imports() -> None:
    """Inside the package only relative imports, aiohttp and the stdlib are used."""
    allowed = {
        "abc",
        "aiohttp",
        "collections",
        "dataclasses",
        "datetime",
        "enum",
        "hashlib",
        "hmac",
        "http",
        "json",
        "re",
        "time",
        "typing",
        "urllib",
    }
    for path in PACKAGE_DIR.rglob("*.py"):
        for module in _imported_modules(path):
            if module.startswith("."):
                continue
            assert module.split(".")[0] in allowed, f"{path.name}: {module}"


def test_py_typed_marker() -> None:
    """The package ships a py.typed marker."""
    assert (PACKAGE_DIR / "py.typed").is_file()


def test_public_exports() -> None:
    """Every name in __all__ exists."""
    for name in fluentpet_api.__all__:
        assert hasattr(fluentpet_api, name), name
