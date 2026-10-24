"""WS-C — Aircall adapter (inbound call events → the call log and missed-call inquiries).

Off unless the server switch ``AIRCALL_ENABLED`` is on *and* the shop has saved its Aircall
webhook token (Settings → Calls & telephony). Aircall doesn't sign webhooks with an HMAC: every
payload carries the webhook's own ``token``, which we compare in constant time with the token
this shop saved (encrypted in org_creds as ``aircall_voice``). The shop is picked by the
``org_id`` in the URL and proven by that token; nothing in the payload chooses the tenant.

Payload envelope (Aircall docs, "Webhooks → Payload"):
    {"resource": "call", "event": "call.ended", "timestamp": 1585001000,
     "token": "<webhook token>", "data": {<Call object>}}
Call object fields used: id, direction (inbound|outbound), status (initial|answered|done),
raw_digits (the other party, international format or "anonymous"), number.digits (our line),
started_at / answered_at / ended_at (UNIX seconds; answered_at is null when nobody picked up),
missed_call_reason (out_of_opening_hours, short_abandoned, abandoned_in_ivr,
abandoned_in_classic, no_available_agent, agents_did_not_answer).

Aircall disables a webhook that keeps failing, so events we don't act on still get a 200.
"""
import hmac
from typing import Optional

from backend.config import settings

PROVIDER = "aircall_voice"
HANDLED = ("call.created", "call.answered", "call.ended", "call.voicemail_left")
MISSED_REASONS = ("out_of_opening_hours", "short_abandoned", "abandoned_in_ivr",
                  "abandoned_in_classic", "no_available_agent", "agents_did_not_answer")


def enabled() -> bool:
    return bool(getattr(settings, "AIRCALL_ENABLED", False))


def token_ok(saved: Optional[str], presented) -> bool:
    if not saved or not isinstance(presented, str) or not presented:
        return False
    return hmac.compare_digest(saved.encode(), presented.encode())


def sid(call_id) -> str:
    return f"aircall:{call_id}"


def parse(payload: dict) -> Optional[dict]:
    """The fields we need from a call event, or None for anything else."""
    if not isinstance(payload, dict) or payload.get("resource") != "call":
        return None
    event = payload.get("event")
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    if event not in HANDLED or data.get("id") in (None, ""):
        return None
    number = data.get("number") if isinstance(data.get("number"), dict) else {}
    other = data.get("raw_digits")
    other = None if (not isinstance(other, str) or other.strip().lower() == "anonymous") else other.strip()
    answered, ended = data.get("answered_at"), data.get("ended_at")
    missed = (event == "call.voicemail_left" or data.get("missed_call_reason") in MISSED_REASONS
              or (event == "call.ended" and not answered))
    duration = 0
    if isinstance(answered, (int, float)) and isinstance(ended, (int, float)) and ended > answered:
        duration = int(ended - answered)
    return {"event": event, "sid": sid(data["id"]),
            "direction": "outbound" if data.get("direction") == "outbound" else "inbound",
            "other": other, "line": number.get("digits") if isinstance(number.get("digits"), str) else None,
            "answered": bool(answered), "missed": bool(missed), "duration_s": duration}
