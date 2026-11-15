# Civenthra

[简体中文](README.zh-CN.md) | English

[![Open-source audit](https://github.com/civenthra/civenthra/actions/workflows/open-source-audit.yml/badge.svg)](https://github.com/civenthra/civenthra/actions/workflows/open-source-audit.yml)
[![CodeQL](https://github.com/civenthra/civenthra/actions/workflows/codeql.yml/badge.svg)](https://github.com/civenthra/civenthra/actions/workflows/codeql.yml)
[![License](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)

Civenthra is a local-first, source-grounded offline knowledge prototype. It
keeps material that users lawfully possess or are authorized to use on their
own machine, retrieves relevant passages, and shows the evidence behind an
answer.

This repository contains source code, tests, synthetic examples, and safety
boundaries. It does **not** distribute books, translations, model weights,
indexes, voices, portraits, account data, or generated media.

## Current boundaries

- This is a research and education prototype, not a certified aerospace,
  medical, mental-health, rescue, or equipment-operation system.
- Traceable citations do not prove that an answer is correct. Verify important
  claims against the source and its operating context.
- `civenthra.airgap` is a process-local outbound socket restriction and check.
  It is not an operating-system firewall, isolated network, or physical air gap.
- Operators are responsible for the provenance, license, territory, and
  permitted use of every library item and model they configure.
- Operational medical, food, water, electrical, structural, and survival
  questions fail closed unless retrieved passages carry a current documented
  expert review. Historical material is never promoted to modern instructions.

## Architecture

```text
ingest -> index -> retrieve -> local model -> cited answer
```

- `civenthra/ingest.py`: clean, segment, and index local material.
- `civenthra/index.py`: BM25 retrieval with optional local vector reranking.
- `civenthra/rag.py`: ground local-model answers in retrieved evidence.
- `civenthra/cli.py`: command-line interface.
- `civenthra/serve.py`: loopback-only local web service.
- `civenthra/airgap.py`: process-local network restriction and verification.
- `civenthra/scene_backend.py`: optional local scene-generator adapter.

## Languages

English and Simplified Chinese are supported for answer prompts. `auto` selects
the response language from the question; callers may explicitly request `en` or
`zh-CN`. Source passages stay in their original language and translated text is
labelled rather than silently replacing the original.

```powershell
python -m civenthra.cli --lang en -q "What does the local evidence say?"
python -m civenthra.cli --lang zh-CN -q "本地资料有什么依据？"
```

The public web API also accepts `"language": "auto"`, `"en"`, or `"zh-CN"`.
The legacy browser chrome is still primarily Chinese; translating that interface
is tracked separately from the now bilingual answer path.

## Local setup

1. Copy `config.example.json` to `config.local.json`.
2. If a restricted optional backend is needed, copy
   `authorizations.example.json` to the private `authorizations.local.json` and
   replace every pending placeholder with verified evidence.
3. Install the dependency-free core package.
4. Add paths to lawfully obtained local material, models, and llama.cpp.
5. Build the local index and start the CLI or loopback web service.

```powershell
Copy-Item config.example.json config.local.json
Copy-Item authorizations.example.json authorizations.local.json
python -m pip install --no-deps .
python -m civenthra.ingest
python -m civenthra.cli --lang auto
```

Voice is optional and isolated from the core package. Review the dependency and
network implications before installing `.[voice]` or `.[voice-edge]`.

Local configuration, corpora, indexes, models, and outputs are excluded from
Git. Never commit secrets, cookies, platform identifiers, reference voices, or
portraits.

## Public collection sources

The optional networked collector publishes its source registry and collection
method without publishing books, private manifests, accounts, or cookies. It
supports official metadata snapshots and OAI-PMH discovery, then requires a
territory-specific per-item rights review before any book file can enter local
quarantine.

All seven registered metadata entries have a dated [live verification record](docs/COLLECTION_LIVE_CHECK.md).
The [historical source inventory and STEM method](docs/HISTORICAL_SOURCES.md)
also document channels used by the original project and their current integration status.

```powershell
python -m civenthra.collect list
python -m civenthra.collect sync-catalog project-gutenberg `
  --output E:/civenthra-staging/catalogs/pg_catalog.csv.gz
python -m civenthra.collect sync-catalog doab `
  --output E:/civenthra-staging/catalogs/doab.jsonl --limit 500
```

Restricted, dark, lending-only, encrypted, LCP, DRM, non-allowlisted, or
incompletely reviewed items fail closed. See `docs/COLLECTION.md` for sources,
the review manifest, quarantine flow, and the path toward 100,000 items.

## Optional scene generation

`civenthra.scene_backend` can call a separately installed local scene generator.
MiniMax H3 and WanGP are not open-source components of Civenthra. Their code,
weights, and installers are not distributed here, and H3 remains disabled until
an approved, unexpired structured authorization covers the configured territory,
scope, license digest, evidence reference, and redistribution constraints.

Their territory, redistribution, and commercial terms differ from Apache-2.0.
Read `THIRD_PARTY_NOTICES.md` and the current upstream licenses before use. Core
learning content must remain usable when scene generation is unavailable.

## Voice and portraits

Voice cloning fails closed. Possession of a reference recording is not consent;
the operator must record explicit authorization and comply with the upstream
model license. The local approval is tied to the reference recording's SHA-256,
expires on a recorded date, and requires the XTTS non-commercial constraint.
The public repository contains no voice samples, portraits, avatar models, or
platform credentials.

`canon/rights-manifest.json` denies public distribution of every catalog book,
translation, audio, video, voice, and likeness unless a complete approval
record is added. `scripts/validate_rights.py` enforces that boundary.

## Synthetic demo

The reproducible golden path contains only project-authored fictional text:

```powershell
python examples/synthetic_demo.py --lang en
python examples/synthetic_demo.py --lang zh-CN
```

## Release verification

```powershell
python scripts/open_source_audit.py
python scripts/validate_rights.py
python scripts/git_history_audit.py
python -m pytest -q
python scripts/build_sbom.py --output out/civenthra.spdx.json
```

`release-manifest.txt` is an exact allowlist. A newly tracked or historically
deleted file fails the release gates until the public history is rebuilt and
reviewed. Private media can be
checked separately with:

```powershell
python scripts/media_integrity.py <private-output-dir>
```

Empty or corrupt media must never be cited as a working demonstration. See
`docs/OPEN_SOURCE_READINESS.md` for the complete release boundary.

## Community and direction

- Architecture: `docs/ARCHITECTURE.md`
- Public collection: `docs/COLLECTION.md`
- Roadmap: `ROADMAP.md`
- Changelog: `CHANGELOG.md`
- Contributing: `CONTRIBUTING.md`
- Governance and maintainers: `GOVERNANCE.md`, `MAINTAINERS.md`
- Security and support: `SECURITY.md`, `SUPPORT.md`

## License, security, and contributions

Civenthra source code is licensed under Apache-2.0; see `LICENSE`. That
license does not relicense third-party software, models, source material, or
generated output. See `THIRD_PARTY_NOTICES.md`, `SECURITY.md`, and
`CONTRIBUTING.md` before opening a contribution.

## Direction

The long-term direction is an offline knowledge, education, life-guidance, and
logging system for long-duration disconnected environments, including Mars
habitation research scenarios. This direction does not imply present mission
readiness, professional certification, or safety-critical qualification.
