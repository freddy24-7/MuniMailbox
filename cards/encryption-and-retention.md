# Encryption at rest, restoration and retention

**Status:** Overview · **Stages:** 3 · **Requirements:** FR-12, GR-2, GR-3 · **Code:** `app/privacy/` (mapping store, restore)

## In one sentence
The table that links each placeholder back to the real value is stored encrypted, opened
only in the backend for roles allowed to see it, and deleted after a set period.

## Why it matters in this app
The `PiiMapping` table is the key that turns pseudonymized text back into personal data.
Protecting it, limiting who can use it and limiting how long it exists are three separate
controls on the same risk.

## Core ideas
- **Encryption at rest** — data is unreadable on disk without the key. Protects against a
  stolen database file or backup, not against a compromised running app.
- **Symmetric authenticated encryption** — one key encrypts and decrypts, and tampering
  with the ciphertext is detected (e.g. AES-GCM or Fernet).
- **Key management** — the key lives outside the database and outside the repository
  (GR-10). Whoever has key *and* data has everything.
- **Field-level encryption** — only sensitive columns are encrypted, so the rest stays queryable.
- **Restoration (de-pseudonymization)** — replacing placeholders with original values,
  in the backend, at display time, per request (FR-12, GR-3).
- **Data minimisation and storage limitation** — GDPR principles: keep only what is
  needed, only as long as needed.
- **Retention job** — a scheduled task that deletes mappings older than the configured period (GR-2).
- **Crypto-shredding** — deleting the key (or the mapping) makes the remaining data
  permanently pseudonymous.

## In this project
- Per-email mapping: `email_id`, `placeholder`, `encrypted_value`, `entity_type`.
- The original email body is also stored encrypted.
- Caseworkers get restored text; auditors never do — the restore function itself checks the role.
- Stored drafts and all LLM inputs contain placeholders only (acceptance criterion).

## Questions to be able to answer
- Which attacks does encryption at rest stop, and which does it not?
- After the mapping is deleted, what can a caseworker still see of an old email?
- Why restore in the backend and not send the mapping to the browser?
- What happens to all stored mappings if the encryption key is lost? Rotated?
- A caseworker edits a restored draft and saves it — how does it get back to placeholders?

## Related cards
[pii-redaction](pii-redaction.md) · [rbac](rbac.md) · [config-and-secrets](config-and-secrets.md) · [human-in-the-loop-review](human-in-the-loop-review.md)

## Notes from the build
_Empty — filled in during Stage 3._
