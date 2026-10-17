"""Tests for the FluentPet Connect constants."""

from custom_components.fluentpet.const import DOMAIN


def test_domain() -> None:
    """The integration domain matches the manifest."""
    assert DOMAIN == "fluentpet"
