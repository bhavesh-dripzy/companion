"""Zepto image-URL encoding — names with '/', '|', spaces break the ik-seo CDN path (404).

Verified live: the raw URL is unreachable, the percent-encoded one returns 200.
"""

from __future__ import annotations

from core.agent.providers.zepto import _safe_image_url

_BASE = "https://cdn.zeptonow.com/production/ik-seo/tr:w-470/cms/product_variant"
_UUID = "b73a9ba7-d9a9-4603-adaa-8b715d3691a8"


def test_encodes_name_with_slash_and_spaces():
    url = f"{_BASE}/{_UUID}/Brinjal Small / Nagpur.jpeg"
    out = _safe_image_url(url)
    assert out.endswith("/Brinjal%20Small%20%2F%20Nagpur.jpeg")
    assert " " not in out  # no raw spaces left


def test_encodes_pipe():
    url = f"{_BASE}/{_UUID}/Frubon HiLo | Low Fat Milk.jpg"
    assert "%7C" in _safe_image_url(url)


def test_clean_name_unchanged():
    url = f"{_BASE}/{_UUID}/Milk.jpg"
    assert _safe_image_url(url) == url  # nothing to encode


def test_non_matching_or_empty_passthrough():
    assert _safe_image_url("https://example.com/foo.png") == "https://example.com/foo.png"
    assert _safe_image_url("") == ""
