## Summary

Describe the problem, the smallest solution, and any user-visible change.

## Verification

List the exact commands run and their results. Use synthetic fixtures only.

## Release-safety checklist

- [ ] I added or updated regression tests for behavior changes.
- [ ] I ran `python scripts/open_source_audit.py` and `python scripts/validate_rights.py`.
- [ ] I ran the relevant tests; the full suite is required for release-boundary changes.
- [ ] I added every new public file to `release-manifest.txt`.
- [ ] I did not add books, translations, private paths, logs, credentials, voices,
      portraits, model weights, generated media, or account data.
- [ ] I updated both English and Chinese user-facing documentation when needed.
- [ ] I documented third-party terms and rights evidence when applicable.

## Risk and rollback

State the affected trust boundary, remaining risk, and how to revert safely.
