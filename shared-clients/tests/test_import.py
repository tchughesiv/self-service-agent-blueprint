"""Smoke test: shared_clients package is importable."""


def test_import() -> None:
    """Verify shared_clients can be imported."""
    import shared_clients  # noqa: F401

    assert shared_clients.__all__ is not None
