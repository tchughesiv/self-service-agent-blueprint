"""Tests for shared_models.utils helpers."""

from shared_models.utils import (
    normalize_zammad_rest_api_base,
    zammad_rest_authorization_headers,
    zammad_rest_json_headers,
)


def test_normalize_zammad_rest_api_base() -> None:
    assert (
        normalize_zammad_rest_api_base("https://z.example")
        == "https://z.example/api/v1"
    )
    assert (
        normalize_zammad_rest_api_base("https://z.example/api/v1")
        == "https://z.example/api/v1"
    )
    assert (
        normalize_zammad_rest_api_base("https://z.example/api/v1/")
        == "https://z.example/api/v1"
    )


def test_zammad_rest_headers() -> None:
    assert zammad_rest_authorization_headers("abc") == {
        "Authorization": "Token token=abc",
    }
    assert zammad_rest_json_headers("abc") == {
        "Authorization": "Token token=abc",
        "Content-Type": "application/json",
    }
