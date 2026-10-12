"""Tests for the shared lenient LLM JSON extractor."""

import json

import pytest

from strelitzia.llm_json import extract_json


def test_plain_object():
    assert extract_json('{"a": 1}') == {"a": 1}


def test_plain_array():
    assert extract_json('[1, 2, 3]') == [1, 2, 3]


def test_json_fence():
    assert extract_json('Here you go:\n```json\n{"a": 1}\n```\nDone.') == {"a": 1}


def test_bare_fence():
    assert extract_json('```\n{"a": [1, 2]}\n```') == {"a": [1, 2]}


def test_prose_around_object():
    assert extract_json('Sure! The result is {"title": "x", "n": 2} hope that helps.') == {"title": "x", "n": 2}


def test_think_block_before_json():
    text = '<think>maybe {"wrong": true}</think>\n{"right": true}'
    assert extract_json(text) == {"right": True}


def test_invalid_escape():
    assert extract_json(r'{"name": "snake\_case"}') == {"name": "snake_case"}


def test_trailing_junk_after_closing_brace():
    assert extract_json('{"a": 1}}') == {"a": 1}
    assert extract_json('{"a": 1} -- end of response') == {"a": 1}


def test_nothing_parseable_raises():
    with pytest.raises(json.JSONDecodeError):
        extract_json("no json here at all")
    with pytest.raises(ValueError):
        extract_json("{not: valid")


def test_prose_around_array_stays_array():
    assert extract_json('Verdicts: [{"index": 1, "relevant": true}] done') == [{"index": 1, "relevant": True}]


def test_null_is_valid_json():
    assert extract_json("null") is None


def test_trailing_junk_in_json_fence_with_later_fence():
    text = '```json\n{"strategy": "css_selectors"}}\n```\nExample:\n```\nfoo\n```'
    assert extract_json(text) == {"strategy": "css_selectors"}
