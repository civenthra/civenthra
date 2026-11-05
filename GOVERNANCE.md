# Governance

Civenthra is currently maintainer-led. People with GitHub write access are
maintainers. The public maintainer identity is **Civenthra Maintainers / 文明穹顶维护组**;
[@stonestorm2024](https://github.com/stonestorm2024) is the interim release lead
until that role is delegated in a public issue. Membership and succession are
recorded in `MAINTAINERS.md`.

## Decisions

- Small, reversible changes use pull-request review and passing release gates.
- Architecture, safety boundaries, data formats, and license policy require a
  written decision record and at least one maintainer approval.
- Adding public book text, translations, media, voices, likenesses, or
  safety-critical instructions requires both rights evidence and the applicable
  expert review. Automation fails closed when either is missing.
- Security reports remain private until a fix and disclosure plan are ready.
- A maintainer with a personal or financial conflict must disclose it and
  abstain when another maintainer is available.

## Releases

The release lead verifies the exact manifest, tests, rights audit, history
audit, SBOM, checksums, and provenance attestation. A release may be withdrawn
for security, rights, privacy, or safety defects. No release process grants
permission to publish excluded local material.

## Changes to governance

Governance changes use a pull request with a public rationale and a seven-day
comment period once the project has more than one active maintainer.
