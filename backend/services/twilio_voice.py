"""D1 — Twilio Programmable Voice adapter (REST Calls API, webhook signatures, TwiML).

Each shop connects its OWN Twilio account; the keys live encrypted in org_creds under the
internal provider ``twilio_voice`` and never leave the server. Saving the keys probes Twilio
to learn whether they are *test* credentials (magic numbers, nothing dials, nothing is billed)
or *live* ones, and live ones are refused unless ENVIRONMENT=production and
ALLOW_LIVE_TELEPHONY=true.

How the probe tells them apart (Twilio's documented test-credential behaviour):
  * GET /Accounts/{sid}.json  → 200 with live keys; 403 (error 20008, "not accessible with
    test account credentials") with test keys; 401 with wrong keys.
  * For test keys we confirm with POST /Calls.json from the magic +15005550006 number, which
    Twilio answers with a fake queued call. With live keys that request would fail (21210:
    the magic number isn't on the account), so the probe can never dial anyone.

Tests swap the HTTP layer by setting ``transport`` to an ``httpx.MockTransport``.
"""
import base64
import hashlib
import hmac
from typing import Optional
from urllib.parse import urlencode
from xml.sax.saxutils import escape, quoteattr

import httpx

from backend.config import settings

API_BASE = "https://api.twilio.com/2010-04-01"
TEST_FROM_NUMBER = "+15005550006"   # Twilio's magic "valid number" for test credentials
TIMEOUT_S = 10.0

transport: Optional[httpx.AsyncBaseTransport] = None   # tests inject a MockTransport


class TwilioError(Exception):
    """A Twilio API failure with a message safe to show the shop."""

    def __init__(self, message: str, status: int = 502, code: Optional[int] = None):
        super().__init__(message)
        self.message = message
        self.status = status
        self.code = code


def live_allowed() -> bool:
    return settings.is_production and bool(settings.ALLOW_LIVE_TELEPHONY)


def _client(account_sid: str, auth_token: str) -> httpx.AsyncClient:
    return httpx.AsyncClient(base_url=API_BASE, auth=(account_sid, auth_token),
                             timeout=TIMEOUT_S, transport=transport)


def _json(r: httpx.Response) -> dict:
    try:
        body = r.json()
    except ValueError:
        return {}
    return body if isinstance(body, dict) else {}


def _error_from(r: httpx.Response) -> TwilioError:
    body = _json(r)
    code = body.get("code")
    msg = body.get("message") or f"Twilio returned HTTP {r.status_code}"
    if r.status_code == 401:
        return TwilioError("Twilio rejected the Account SID or auth token.", 422, code)
    return TwilioError(f"Twilio: {msg}" + (f" (error {code})" if code else ""), 502, code)


async def probe(account_sid: str, auth_token: str) -> dict:
    """Validate keys against Twilio and classify them. Returns {"mode": "test"|"live",
    "account_name": str|None}. Raises TwilioError for wrong keys or an unreachable API."""
    if not account_sid.startswith("AC") or len(account_sid) != 34:
        raise TwilioError("The Account SID should start with AC and be 34 characters.", 422)
    try:
        async with _client(account_sid, auth_token) as c:
            r = await c.get(f"/Accounts/{account_sid}.json")
            if r.status_code == 200:
                return {"mode": "live", "account_name": _json(r).get("friendly_name")}
            if r.status_code == 403 and _json(r).get("code") == 20008:
                # Test keys: confirm they're valid with a magic-number call (fake, free).
                t = await c.post(f"/Accounts/{account_sid}/Calls.json", data={
                    "From": TEST_FROM_NUMBER, "To": TEST_FROM_NUMBER,
                    "Url": "http://demo.twilio.com/docs/voice.xml"})
                if t.status_code == 201:
                    return {"mode": "test", "account_name": None}
                raise _error_from(t)
            raise _error_from(r)
    except httpx.HTTPError as e:
        raise TwilioError(f"Couldn't reach Twilio ({e.__class__.__name__}). Try again.", 502) from e


async def create_call(account_sid: str, auth_token: str, *, to: str, from_: str,
                      url: str, status_callback: str) -> dict:
    """POST /Calls.json. Returns Twilio's call resource (sid, status, …)."""
    try:
        async with _client(account_sid, auth_token) as c:
            r = await c.post(f"/Accounts/{account_sid}/Calls.json", data={
                "To": to, "From": from_, "Url": url, "Method": "POST",
                "StatusCallback": status_callback, "StatusCallbackMethod": "POST",
                "StatusCallbackEvent": ["initiated", "ringing", "answered", "completed"],
            })
    except httpx.HTTPError as e:
        raise TwilioError(f"Couldn't reach Twilio ({e.__class__.__name__}). Try again.", 502) from e
    if r.status_code != 201:
        raise _error_from(r)
    return _json(r)


# ── Webhook signatures ───────────────────────────────────────────────────────
def signature(auth_token: str, url: str, params: list[tuple[str, str]]) -> str:
    """X-Twilio-Signature: base64(HMAC-SHA1(token, url + key+value pairs)). Like Twilio's own
    validator, keys are sorted and a repeated key's distinct values are sorted too."""
    payload = url + "".join(k + v for k, v in sorted(set(params)))
    return base64.b64encode(hmac.new(auth_token.encode(), payload.encode(), hashlib.sha1)
                            .digest()).decode()


def signature_ok(auth_token: str, presented: str, urls: list[str],
                 params: list[tuple[str, str]]) -> bool:
    if not (auth_token and presented):
        return False
    return any(hmac.compare_digest(signature(auth_token, u, params), presented) for u in urls)


def request_urls(request) -> list[str]:
    """URLs Twilio may have signed for this request: the one we received, and the public one
    (behind a TLS-terminating proxy the app sees http://internal-host, Twilio signed https://…)."""
    path_q = request.url.path + (f"?{request.url.query}" if request.url.query else "")
    return list(dict.fromkeys([str(request.url), settings.public_base + path_q]))


def sms_refusal(creds: dict, *, whatsapp: bool = False) -> Optional[str]:
    """S7 — the live guard for Twilio SMS/WhatsApp (provider `twilio`), same rule as voice: real,
    billed messages only with ENVIRONMENT=production and ALLOW_LIVE_TELEPHONY=true. Elsewhere only
    Twilio's test setup is allowed, i.e. SMS from the magic test number, which never delivers or
    bills (with live keys Twilio rejects that number). Returns the refusal, or None if allowed."""
    if live_allowed():
        return None
    if not whatsapp and (creds.get("from") or settings.TWILIO_FROM or "").strip() == TEST_FROM_NUMBER:
        return None
    return ("Live Twilio texting is off on this server. Use Twilio test credentials with "
            f"{TEST_FROM_NUMBER} as the SMS number, or set ENVIRONMENT=production and "
            "ALLOW_LIVE_TELEPHONY=true.")


# ── URLs and TwiML ───────────────────────────────────────────────────────────
def webhook_url(kind: str, org_id: str, call_id: Optional[str] = None) -> str:
    """Public URL Twilio calls back on. kind: 'voice' | 'status'."""
    q = {"org_id": org_id}
    if call_id:
        q["call_id"] = call_id
    return f"{settings.public_base}/api/comms/twilio/{kind}?{urlencode(q)}"


def twiml(*verbs: str) -> str:
    return "<?xml version=\"1.0\" encoding=\"UTF-8\"?><Response>" + "".join(verbs) + "</Response>"


def say(text: str) -> str:
    return f"<Say>{escape(text)}</Say>"


def dial(number: str, *, caller_id: Optional[str], action: str, number_status_cb: str,
         timeout: int = 25) -> str:
    attrs = f" action={quoteattr(action)} method=\"POST\" timeout=\"{timeout}\""
    if caller_id:
        attrs += f" callerId={quoteattr(caller_id)}"
    return (f"<Dial{attrs}><Number statusCallback={quoteattr(number_status_cb)} "
            f"statusCallbackMethod=\"POST\" statusCallbackEvent=\"answered\">"
            f"{escape(number)}</Number></Dial>")
