# A4 Xero · A7 VoIP/Campaigns · SSO groundwork (gated seams)

All three are **real code, gated** — they never fire against external services
without credentials, so they're testable at the seam here and wire to live
providers in a real environment.

## A4 — Xero accounting connector
`services/xero.py` already existed (status/connect/callback/push/disconnect);
this pass adds the **router** `/api/integrations/xero/*` mirroring the shipped
QBO connector. Gated on `XERO_CLIENT_ID/SECRET` — `connect` returns 400 "not
configured" until set. `sync/{invoice|contact|payment}/{id}` pushes a local doc.

## A7 — VoIP + marketing campaigns
- **VoIP** (`Call` model): `POST /api/comms/calls/webhook?org_id=` ingests a
  provider (Aircall/Twilio/Zadarma) call event and **matches the client by
  phone** (so an inbound call can pop the client record). `GET /api/comms/calls`
  lists them. The provider webhook is the seam — real delivery needs the provider
  configured.
- **Campaigns** (`Campaign` model): create SMS/email campaigns, `POST
  .../send` blasts to a **segment** (all|loyalty|lapsed), honoring per-client
  consent, over the existing channel adapters. Under the simulated adapter it
  records recipients without delivering; a real SMS/email gateway delivers.

## SSO groundwork
`SsoConnection` (oidc|saml, issuer, client_id/secret, email_domain, enabled).
`/api/sso/config` CRUD (secrets redacted in responses), `/api/sso/discover?email=`
routes a login to the org's IdP by email domain, `/api/sso/authorize/{id}` returns
the OIDC authorization URL a real flow would use. The **assertion/token exchange
is the gated seam** — completing sign-in needs a live IdP. (MFA/TOTP already
shipped in v0.21.0; this is the federated-identity half of B1.)

## Tests
`backend/tests/test_a4_a7_sso.py` (4): Xero status + gated-connect 400; VoIP
webhook client-match (matched + unmatched); campaign loyalty-segment send (2 of 3)
+ no-double-send; SSO config + domain discovery + gated authorize URL + secret
redaction. Suite 124. Migration +3 tables +2 cols (schema 99).

## Honest limits (need a real environment)
Xero/VoIP live delivery need provider credentials + webhooks; SSO completion needs
an IdP. These are wired and stub-tested, not live-fired — same status as the
shipped Square/SumUp/QBO/Gmail seams.
