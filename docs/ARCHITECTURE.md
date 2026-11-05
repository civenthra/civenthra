# Architecture

## Trust boundary

Civenthra keeps operator-provided books, indexes, models, reference media, and
generated outputs outside Git. The public repository contains adapters and
synthetic fixtures. Core retrieval works without voice, avatar, or scene
generation.

```text
private local inputs
        |
        v
ingest -> local index -> retrieve -> safety gate -> local model -> cited answer
                         |              |
                         |              +-> deny unreviewed high-risk guidance
                         +-> original evidence remains visible

optional sidecars: loopback + per-run token + explicit local authorization
release boundary: manifest + rights ledger + history scan + SBOM + checksums
```

## Components

- `civenthra.ingest` normalizes local sources and writes rebuildable indexes.
- `civenthra.index` provides lexical retrieval with optional local reranking.
- `civenthra.rag` constructs evidence-grounded prompts and citations.
- `civenthra.safety` blocks high-risk operational output without current review.
- `civenthra.serve` exposes the local UI on loopback only.
- `civenthra.voice` and the two sidecars use ephemeral local API tokens.
- `civenthra.scene_backend` invokes a separately installed H3/WanGP stack only
  after a structured private authorization passes.

## Deliberate separations

- Source text and translations are not part of the code release.
- Apache-2.0 covers this repository, not third-party models or their outputs.
- Process-local network restrictions are defense in depth, not an OS air gap.
- Historical evidence is not automatically actionable modern guidance.
- “One-minute discipline” publishing and long-horizon survival modules consume
  reviewed outputs; they do not weaken ingestion, rights, or safety gates.

See `docs/SAFETY_MODEL.md`, `docs/OPEN_SOURCE_READINESS.md`, and
`THIRD_PARTY_NOTICES.md` for the enforceable boundaries.
