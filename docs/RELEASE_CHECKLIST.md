# Release checklist

## Source and identity

- [ ] Release branch is an orphan branch with exactly one root commit.
- [ ] Root commit uses the public maintainer identity, not a workstation identity.
- [ ] `git rev-list --max-parents=0 HEAD` returns exactly one root commit.
- [ ] No development branch or tag is configured for push.
- [ ] Repository name, URLs, package name, CLI, and bilingual docs use Civenthra.

## Local gates

- [ ] `python scripts/open_source_audit.py`
- [ ] `python scripts/validate_rights.py`
- [ ] `python scripts/git_history_audit.py`
- [ ] `python -m pytest -q`
- [ ] English and Chinese synthetic demos
- [ ] Wheel build and `scripts/audit_archive.py`
- [ ] Clean-tree SPDX SBOM and `SHA256SUMS`
- [ ] Repeat every gate in a fresh clone of the orphan branch.

## Rights, safety, and dependencies

- [ ] No book text, translation, media, identity data, model, index, credential,
      browser profile, or private authorization record is tracked.
- [ ] `canon/rights-manifest.json` covers all public content assets.
- [ ] `THIRD_PARTY_NOTICES.md` matches the exact reviewed upstream revisions.
- [ ] Hash-locked dependencies and pinned GitHub Actions have been rechecked.
- [ ] High-risk guidance and restricted optional backends still fail closed.

## GitHub settings

- [ ] Default branch is `main`; workflow token permissions are read-only.
- [ ] Branch rules require pull requests, status checks, and code-owner review.
- [ ] Secret scanning, push protection, Dependabot, and private vulnerability
      reporting are enabled where the repository plan supports them.
- [ ] Topics, description, social preview, and community profile are reviewed.
- [ ] The private repository is inspected before any visibility change.

## Release evidence

- [ ] Tag and release notes match `CHANGELOG.md`.
- [ ] Wheel, SBOM, checksums, and provenance artifacts correspond to the same commit.
- [ ] Publication is explicitly approved by the repository owner.
