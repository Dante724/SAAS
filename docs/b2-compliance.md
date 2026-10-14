# B2 — Audit Trail + GDPR (data export & erasure)

## Audit trail
`AuditLog` existed but was **never written** (dormant table). Now live:
- **Service** `backend/services/audit.py`: `record(...)` appends who/what/entity/
  diff inside the caller's transaction (rolled back if the business action fails;
  best-effort, never breaks the action). `diff_fields(before, after, keys)` helper.
- **Wired** into order create, order status change (captures `[old,new]`), and
  client create — the highest-value mutations. More call sites can adopt the same
  one-liner.
- **Read API** `GET /api/compliance/audit` — filter by entity_type/entity_id/
  action/actor, resolves actor names. Gated `can_manage_settings`.

## GDPR
- **Export** `GET /api/compliance/gdpr/client/{id}/export` (Art. 15/20): portable
  copy of the subject's profile + assets + order history. The export is itself
  audited.
- **Erasure** `POST /api/compliance/gdpr/client/{id}/erase` (Art. 17): anonymizes
  PII (name → tombstone, phone/email/address/notes cleared, asset serial/VIN/HIN/
  IMEI/passcode/color scrubbed) while **retaining financial records** — order
  totals stay intact for accounting. Erasure is audited with a scrub count.

## Tests
`backend/tests/test_compliance.py` (3): audit captures create/status_change with
diffs + entity filtering; GDPR export returns full record + self-audits; erasure
anonymizes PII, keeps the $200 order total, and is audited. Suite 111. No schema
change (AuditLog already existed).

## Note
Right-to-erasure intentionally anonymizes rather than hard-deletes, because
financial/tax records must be retained — the standard GDPR-vs-accounting
reconciliation. A configurable retention window before physical deletion is a
future refinement.
