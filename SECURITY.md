# Security policy

[简体中文](SECURITY.zh-CN.md) | English

Civenthra is local-first. Services bind to loopback by default and raw books,
microphone input, conversations, portraits and voice references must not be
uploaded without an explicit user action.

Do not report vulnerabilities in public issues when a report contains private
paths, logs, account data or media. Use **Security → Report a vulnerability** in
the GitHub repository; this creates a private vulnerability report. The
repository owner must enable GitHub private vulnerability reporting before the
repository becomes public. If that button is unavailable, open a public issue
containing only the words “private security reporting unavailable” and no
technical details, logs, paths, or attachments.

Never commit `config.local.json`, model weights, book files, indexes, platform
receipts, browser profiles, cookies, tokens, private portraits, voice samples or
generated media. Run `python scripts/open_source_audit.py` before every release.

Voice cloning is disabled unless a private structured authorization record is
approved, current, territory- and scope-matched, bound to the reference audio
SHA-256, and compliant with the upstream non-commercial model license. The
project never treats possession of an audio file or a Boolean flag as consent.

## Supported versions

| Version | Security fixes |
| --- | --- |
| Latest release | Supported |
| Older releases and development snapshots | Not supported |

The project is research software and is not certified for medical,
life-support or autonomous safety-critical decisions.
