# Open-source release boundary

## Included

- Local-first runtime source and tests.
- Evidence and safety policies.
- Optional adapters for local model services.
- Synthetic examples that contain no copyrighted book text or personal data.

## Excluded

- Books, OCR, translations and indexes.
- Model weights and virtual environments.
- Portraits, voice references and generated media.
- Platform receipts, analytics, account identifiers and browser profiles.
- Historical operator logs, local paths and dated production checkpoints.

## Scene generation

MiniMax H3 is an optional scene backend for educational products. It generates
the visual scene; Blender/FFmpeg remain deterministic composition backends for
captions, diagrams, overlays and final encoding. A lesson must remain usable
when H3 is unavailable. H3 runs as a separately installed local tool, and its
software/model licenses are not inherited from Civenthra.

H3 is not described as an open-source component. Its Community License excludes
the EU, UK, Republic of Korea and USA and contains downstream-use, redistribution
and commercial conditions. WanGP also has separate commercialization limits.
The public repository therefore ships only a disabled interoperability adapter:
no WanGP code, model weights, default activation, installer or hosted endpoint.
Before enabling it, the operator must review the current upstream licenses for
the deployment territory and intended use. This is a release control, not legal
advice.

`authorizations.example.json` defines the required private record shape. H3 and
XTTS reject missing, pending, expired, territory-mismatched, scope-mismatched,
or license-hash-invalid records. XTTS also rejects a reference recording whose
SHA-256 no longer matches the approved record. The actual
`authorizations.local.json` is ignored and must never be committed.

## Release gate

1. Review every tracked path in `release-manifest.txt`. The audit fails closed
   when Git contains an undeclared file or the manifest contains a stale entry.
2. Run `python scripts/open_source_audit.py` and require zero errors.
3. Run `python scripts/media_integrity.py <private-output-dir>`; never publish or
   cite an empty/corrupt media file as a working demonstration.
4. Run the full test suite, bilingual synthetic demo, packaging smoke test, and
   clean-clone validation.
5. Run `python scripts/validate_rights.py`; book text, translations, media,
   voices and likenesses remain excluded unless evidence is complete.
6. Run `python scripts/git_history_audit.py`; a file deleted from the current
   tree still blocks release while it remains reachable from `HEAD`.
7. Generate the SPDX SBOM only from a clean tree, then generate `SHA256SUMS`;
   use the pinned provenance workflow for a tagged release.
8. Review `THIRD_PARTY_NOTICES.md` against exact model and tool revisions.
9. Inspect the staged diff and downloaded GitHub artifact before publication.

The current local repository can contain ignored private operational material.
Passing the audit proves only that Git's public release candidate is clean; it
does not grant rights to ignored local files.

## Git history boundary

The development history predates the portable defaults and contains local
absolute paths. Do not push, mirror or import the development branches or tags
to a public remote. Publish only the final verified orphan release branch,
whose root commit has no parent and
whose reachable history passes `git_history_audit.py`. Re-run every gate from a
clean clone of that branch immediately before publication.

## GitHub settings gate

Before changing repository visibility to public, the owner must enable private
vulnerability reporting, secret scanning and push protection, Dependabot
alerts, and protected-branch pull-request review. Keep default workflow token
permissions read-only; grant `id-token: write` and `attestations: write` only to
the pinned release-evidence workflow. These settings cannot be proven by files
in this source tree and must be verified in the GitHub repository UI.
