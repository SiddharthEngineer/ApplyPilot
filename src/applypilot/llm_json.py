"""Lenient JSON extraction from LLM responses.

One helper for every stage: tolerates ``<think>`` preambles, markdown fences, prose around
the payload, invalid backslash escapes and trailing junk.
"""

from __future__ import annotations

import json
import re

_INVALID_ESCAPE = re.compile(r'\\([^"\\\/bfnrtu])')


def _try_parse(text: str):
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def _fenced_blocks(text: str) -> list[str]:
    blocks = []
    for part in text.split("```")[1::2]:
        part = part.strip()
        if part.startswith("json"):
            part = part[4:].strip()
        blocks.append(part)
    return blocks


def _outermost(text: str, open_ch: str, close_ch: str) -> str | None:
    start = text.find(open_ch)
    end = text.rfind(close_ch)
    if start != -1 and end > start:
        return text[start:end + 1]
    return None


def extract_json(text: str) -> dict | list:
    """Extract the JSON payload from an LLM response.

    Tries, in order: the whole text, each fenced block, both again with invalid
    backslash escapes removed, the outermost ``{...}`` then ``[...]`` slice, and
    finally trimming trailing characters until something parses.

    Raises:
        json.JSONDecodeError: If nothing parses (a ``ValueError`` subclass).
    """
    text = text.strip()
    if "</think>" in text:
        after = text.split("</think>")[-1].strip()
        if after:
            text = after

    candidates = [text, *_fenced_blocks(text)]
    for cand in candidates:
        if (result := _try_parse(cand)) is not None:
            return result

    fixed = [_INVALID_ESCAPE.sub(r"\1", c) for c in candidates]
    for cand in fixed:
        if (result := _try_parse(cand)) is not None:
            return result

    for open_ch, close_ch in (("{", "}"), ("[", "]")):
        for source in (text, fixed[0]):
            sliced = _outermost(source, open_ch, close_ch)
            if sliced is not None and (result := _try_parse(sliced)) is not None:
                return result

    last = fixed[-1]
    while last.endswith("}") or last.endswith("]"):
        if (result := _try_parse(last)) is not None:
            return result
        last = last[:-1].rstrip()

    raise json.JSONDecodeError("Could not parse JSON", text, 0)
