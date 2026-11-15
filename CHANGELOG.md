# Changelog

All notable changes are documented here. The project follows Semantic
Versioning after the first public release.

## [Unreleased]

### Added

- Public, credential-free source registry for Project Gutenberg, DOAB,
  OpenEdition Books, NCBI Bookshelf, public Internet Archive items, and English
  and Chinese Wikisource dumps.
- Explicit collection CLI for official catalog snapshots, bounded OAI-PMH
  harvesting, reviewed item downloads, SHA-256 receipts, and quarantine-only
  output.
- Fail-closed source host, redirect, size, territory, rights, access, and
  encrypted/LCP/DRM gates with offline tests and a bilingual collection guide.

## [0.1.0] - 2026-11-15

### Added

- Bilingual English and Simplified Chinese answer paths and synthetic demos.
- Local-first ingestion, retrieval, cited answers, safety review gates, and
  optional local voice, avatar, and scene-generation adapters.
- Release allowlist, rights ledger, history audit, SPDX SBOM, checksums, pinned
  CI actions, and hash-locked development dependencies.
- Structured private authorization records for restricted H3 and XTTS use.

### Security

- Loopback services require strict local addressing and per-run tokens.
- Restricted content, private media, unknown public formats, stale approvals,
  and dirty-tree release evidence fail closed.
