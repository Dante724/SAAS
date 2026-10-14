# NEXT TASK

**Phase 3 is feature-complete — all Parts 1–11 are ✅ in CHECKLIST.md.** This
pass fixed a critical auth bug (`token_version` had landed on `Organisation`, so
register/login 500'd) and finished the tail: lost-device logout (Part 10),
`TimeEntry` GPS columns (Part 5), the offline conflict-resolution UI (Part 4),
and a Playwright + Lighthouse e2e suite with a CI workflow (Part 11).

Gate green: ruff clean, **50 pytest pass**, schema builds **82 tables / 0 drift**,
QA 26/47/70. Parts→stock linkage (picker → `kind:product` line → stock deduction
+ pending-stock close guard) was already working and is verified.

**Two things remain before Phase 3 is signed off — both need a real environment,
not the build sandbox:**

1. **Run the e2e/Lighthouse suite in CI.** `tests-e2e/` + `.github/workflows/
   e2e.yml` are committed and runnable. On the first CI run, confirm the
   Playwright specs pass and the Lighthouse PWA/best-practices/a11y thresholds
   hold (≥ 90); tune selectors/thresholds if the first run flags anything.

2. **Live-fire push.** Generate a keypair (`vapid --gen`), set
   `VAPID_PUBLIC_KEY`/`VAPID_PRIVATE_KEY`, subscribe a real device, assign a job
   to that technician, and confirm the push arrives foreground + background. The
   dispatch path is done and unit-tested against a stubbed transport.

**Post-Phase-3 roadmap → see `ROADMAP.md`.** It has two tracks:
Track A (Orderry parity: payments breadth incl. Square/SumUp/terminals, gift
cards + promotions + loyalty points, VIN/IMEI decode, Xero, AI depth, the
Gmail/Outlook email connector, VoIP + campaigns + Zapier, fleet accounts, owner
insights) and Track B (enterprise readiness: SSO/MFA/SCIM + RBAC v2,
audit-everything + GDPR tooling + PII encryption, rate limiting + durable job
queue + media offload to object storage, Prometheus/OTel/Sentry observability,
franchises/white-label, SIEM + bulk APIs) — with a quarter-by-quarter
sequencing recommendation.

**Safest rollback base:** `repairos-32.tar` (Parts 7 & 9). This pass is
`repairos-33.tar` — note it also fixes the register/login regression that was
present in 32, so **33 supersedes 32 for any real use.**
