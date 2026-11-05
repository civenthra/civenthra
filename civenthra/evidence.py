"""Conservative source locators for an existing, unchanged chunk index."""
from __future__ import annotations

from array import array
import hashlib
from pathlib import Path
import re

from .textutil import clean_text, strip_bad_glyphs
from . import textutil


def digest_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def file_digest(path: Path) -> str:
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def compact(text: str) -> str:
    return re.sub(r"\s+", "", text)


CLEANER_SHA256 = file_digest(Path(textutil.__file__))


def same_cleaned_text(left: str, right: str) -> bool:
    # Whitespace folding must not turn 'now here' into 'nowhere', or join
    # separate numbers / mathematical variable names into a different token.
    words = r"[A-Za-z0-9\u0370-\u03ff]+"
    return compact(left) == compact(right) and re.findall(words, left) == re.findall(words, right)


def record_key(record: dict) -> str:
    text = "%s\0%s\0%s\0%s" % (record["no"], record.get("chapter", ""),
                                    record.get("lang", ""), record["text"])
    return digest_bytes(text.encode("utf-8"))


class Source:
    """Offsets count Unicode characters in decoded UTF-8, before cleaning.

    A cleaned chunk is not necessarily a verbatim raw excerpt. Store both and
    verify the exact, versioned cleaning transformation; never invent PDF pages.
    """
    def __init__(self, path: Path, strip_foreign: bool = False):
        self.path = Path(path).resolve()
        data = self.path.read_bytes()
        self.hash = digest_bytes(data)
        self.raw = data.decode("utf-8")  # Invalid UTF-8 must not produce invented offsets.
        self.strip_foreign = strip_foreign
        chars, positions = [], array("I")
        offset = 0
        for line in self.raw.splitlines(keepends=True):
            expected = compact(clean_text(line, strip_foreign=strip_foreign))
            mapped, indices = [], []
            for i, char in enumerate(line):
                for out in strip_bad_glyphs(char, strip_foreign):
                    if not out.isspace():
                        mapped.append(out)
                        indices.append(offset + i)
            if expected and "".join(mapped) == expected:
                chars.extend(mapped)
                positions.extend(indices)
            offset += len(line)
        self.normalized = "".join(chars)
        self.positions = positions

    def locate(self, text: str) -> list[dict]:
        needle = compact(text)
        if not needle:
            return []
        results, cursor = [], 0
        while len(results) < 2:
            start = self.normalized.find(needle, cursor)
            if start < 0:
                break
            left = self.positions[start]
            right = self.positions[start + len(needle) - 1] + 1
            raw_quote = self.raw[left:right]
            if same_cleaned_text(clean_text(raw_quote, self.strip_foreign), text):
                results.append({"source_path": str(self.path), "source_sha256": self.hash,
                                "raw_start": left, "raw_end": right,
                                "raw_excerpt": raw_quote, "strip_foreign": self.strip_foreign,
                                "cleaner_sha256": CLEANER_SHA256,
                                "normalization": "clean-text/whitespace/token-boundaries-v1",
                                "locator_status": "located"})
            cursor = start + 1
        return results


def locate_in_sources(text: str, sources: list[Source]) -> dict:
    hits = []
    for source in sources:
        hits.extend(source.locate(text))
        if len(hits) > 1:
            return {"locator_status": "ambiguous"}
    return hits[0] if hits else {"locator_status": "unresolved"}


def verify(record: dict) -> bool:
    """Fail closed on changed files, wrong offsets, or wrong cleaned content."""
    try:
        data = Path(record["source_path"]).read_bytes()
        if (digest_bytes(data) != record["source_sha256"] or
                record.get("cleaner_sha256") != CLEANER_SHA256 or
                file_digest(Path(textutil.__file__)) != CLEANER_SHA256):
            return False
        raw = data.decode("utf-8")
        start, end = record["raw_start"], record["raw_end"]
        if not 0 <= start < end <= len(raw):
            return False
        excerpt = raw[start:end]
        return (excerpt == record["raw_excerpt"] and
                same_cleaned_text(clean_text(excerpt, bool(record.get("strip_foreign"))), record["text"]))
    except (OSError, UnicodeError, KeyError, TypeError):
        return False
