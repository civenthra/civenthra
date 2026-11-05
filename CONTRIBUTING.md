# Contributing

[简体中文](CONTRIBUTING.zh-CN.md) | English

Thank you for helping make offline knowledge more accessible. Keep pull requests
small, reviewable, and free of private or restricted material.

## Before submitting

1. Do not add books, translations, model weights, indexes, voices, portraits,
   generated media, credentials, account data, or machine-specific paths.
2. Add or update tests for behavior changes.
3. Update both English and Chinese user-facing documentation when behavior or a
   safety boundary changes.
4. Add every deliberately public file to `release-manifest.txt`.
5. Run `python scripts/open_source_audit.py`, `python scripts/validate_rights.py`,
   and `python -m pytest -q`.

Translations should identify the source language and review status. Machine
translation must not be presented as a reviewed or authoritative translation.
Security reports containing private data belong in a private advisory, not a
public issue.
