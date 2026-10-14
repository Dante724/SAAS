# Track B Completion — B1 MFA · B3 · B4 · B5 · B6

One cohesive pass closing the remaining Track-B enterprise/scale gaps. All
additive; existing behaviour unchanged unless a feature is turned on.

## B1 — MFA (TOTP)
Self-contained RFC-6238 TOTP (`services/mfa.py`) — **no pyotp dependency** (not
installable in the locked sandbox). `Employee.mfa_enabled/mfa_secret/
mfa_recovery_codes`. Endpoints: `/api/mfa/{status,enroll,activate,disable}`
(enroll → otpauth URI for the authenticator app; activate confirms a code and
returns 8 one-time recovery codes; disable requires the password). **Login gate**
in `auth.login`: when MFA is on, a valid TOTP or recovery code (via the OAuth2
`client_secret` field) is required, else 401 with `X-MFA-Required: 1`.
*SSO/SAML/OIDC remains open — it can't be live-fired without an IdP.*

## B3 remainder
- **Rate limiting**: dependency-free sliding-window middleware (per-IP, 600/min
  default, health/metrics exempt) → 429 + `Retry-After`.
- **Part/signature offload**: `Part.image_blob_id` + `Order.signature_blob_id`
  pointers and a reusable `media.offload_if_enabled()` helper, so both adopt the
  B3 MediaBlob storage seam with one call (db|local|s3), same as feed media.

## B4 — Observability
- `/metrics` in **Prometheus text format** (request counters by method/status
  class + a latency histogram) via `MetricsMiddleware` — no prometheus_client dep.
- `/api/readyz` readiness probe (verifies DB reachability) alongside the existing
  `/api/health`.

## B5 — White-label
`Organisation.brand_{primary_color,custom_domain,email_from,hide_powered_by}`.
`GET/PUT /api/branding` (authed) + `GET /api/public/branding?domain=` (unauth
resolver so a custom-domain portal/login can theme itself before sign-in).

## B6 — SIEM + bulk API
- `GET /api/siem/audit.ndjson` — audit log as newline-delimited JSON for
  Splunk/Elastic/Datadog ingestion.
- `GET /api/bulk/{entity}` — paginated bulk export (clients, employees) for ETL.

## Tests
`backend/tests/test_track_b_completion.py` (6): MFA enroll/activate/login-gate/
recovery/disable; metrics + readyz; branding roundtrip + public resolver; SIEM
NDJSON + bulk + 404; offload helper db-vs-local. Suite 117. Migration +9 cols
(schema 95).

## Note
Test-harness password bug surfaced (tenant password is `pw-test-12345`) — code
was correct; fixed the test. No `--unsafe-fixes` applied.
