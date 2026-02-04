"""Smoke test: servicenow_bootstrap package is importable."""


def test_import() -> None:
    """Verify servicenow_bootstrap can be imported."""
    import servicenow_bootstrap  # noqa: F401

    assert servicenow_bootstrap.__all__ is not None
