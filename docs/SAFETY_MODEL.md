# Safety model

Civenthra separates historical knowledge from executable high-risk advice.
Questions about medical treatment, food preservation, drinking water, electrical
work, structural work, or survival systems are denied by default. A model prompt
or disclaimer is not treated as a safety control.

High-risk output is enabled only when every detected domain is covered by at
least one retrieved passage whose `safety_review` contains:

- `status: approved_current`;
- the relevant entry in `domains`;
- `reviewed_by`, `reviewed_at`, `valid_until`, and `evidence_uri`;
- an unexpired review date.

Only those reviewed passages are passed to the model. Missing, malformed, or
expired reviews fail closed. Emergency wording adds a deterministic direction
to contact local emergency services; the model is not asked to improvise.

The public `canon/safety-reviews.json` contains no approvals. Operators may keep
qualified review records in a private file selected through `config.local.json`.
Review records are not a substitute for applicable law, local standards,
professional judgment, field inspection, or emergency response.
