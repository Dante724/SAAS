"""Phase E — Customer Feedback.
Three rating systems (5-point, like/dislike, NPS). A status trigger creates a
pending Feedback row (see orders.change_status); the customer submits via a
public token page. Settings define the system + per-band answer messages.
"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import Optional
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from backend.db.database import get_db
from backend.services.dates import parse_date
from backend.models import Feedback, FeedbackSettings, Order
from backend.services.auth import get_current_employee, Employee, require_perm
from backend.services.money import q2
from backend.services.dates import now_utc

router = APIRouter(prefix="/api/feedback", tags=["feedback"])


def _ser_settings(s: FeedbackSettings) -> dict:
    return {
        "id": s.id, "system": s.system, "trigger_status_id": s.trigger_status_id,
        "heading": s.heading, "subheading": s.subheading, "question": s.question,
        "clarify_question": s.clarify_question, "bands": s.bands or {}, "active": s.active,
        "google_review_url": getattr(s, "google_review_url", None),
    }


@router.get("/settings")
async def get_settings(
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    s = (await db.execute(select(FeedbackSettings).where(FeedbackSettings.org_id == current.org_id))).scalar_one_or_none()
    if not s:
        return {"settings": None}
    return {"settings": _ser_settings(s)}


class SettingsIn(BaseModel):
    system: str = "five_point"          # five_point|like|nps
    trigger_status_id: Optional[str] = None
    heading: str = "How did we do?"
    subheading: Optional[str] = None
    question: str = "How would you rate your experience?"
    clarify_question: Optional[str] = "Tell us more"
    bands: dict = {}                    # {"low": "...", "mid": "...", "high": "..."}
    active: bool = False
    google_review_url: Optional[str] = None


@router.put("/settings")
async def save_settings(
    data: SettingsIn,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_manage_settings")),
):
    s = (await db.execute(select(FeedbackSettings).where(FeedbackSettings.org_id == current.org_id))).scalar_one_or_none()
    if not s:
        s = FeedbackSettings(org_id=current.org_id)
        db.add(s)
    s.system = data.system
    s.trigger_status_id = data.trigger_status_id
    s.heading = data.heading
    s.subheading = data.subheading
    s.question = data.question
    s.clarify_question = data.clarify_question
    s.bands = data.bands
    s.active = data.active
    s.google_review_url = (data.google_review_url or "").strip() or None
    await db.commit()
    return {"ok": True}


@router.get("/list")
async def list_feedback(
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    rows = (await db.execute(select(Feedback).where(Feedback.org_id == current.org_id)
        .order_by(Feedback.created_at.desc()).limit(200))).scalars().all()
    orders = {o.id: o.order_number for o in (await db.execute(select(Order).where(Order.org_id == current.org_id))).scalars().all()}
    submitted = [f for f in rows if f.submitted_at]
    # NPS calc (if any nps responses)
    nps_resp = [f for f in submitted if f.system == "nps" and f.rating is not None]
    nps = None
    if nps_resp:
        promoters = sum(1 for f in nps_resp if f.rating >= 9)
        detractors = sum(1 for f in nps_resp if f.rating <= 6)
        nps = round((promoters - detractors) / len(nps_resp) * 100)
    # 5-point average
    five = [f for f in submitted if f.system == "five_point" and f.rating is not None]
    avg5 = q2(sum(f.rating for f in five) / len(five)) if five else None
    return {
        "items": [{"id": f.id, "order_number": orders.get(f.order_id, "—"), "system": f.system,
                   "rating": f.rating, "comment": f.comment,
                   "submitted_at": f.submitted_at.isoformat() if f.submitted_at else None,
                   "pending": f.submitted_at is None} for f in rows],
        "nps": nps, "avg_five_point": avg5,
        "response_rate": round(len(submitted) / len(rows) * 100) if rows else 0,
    }


# ── Public submission (no auth) ──

@router.get("/public/{token}")
async def public_form(token: str, db: AsyncSession = Depends(get_db)):
    fb = (await db.execute(select(Feedback).where(Feedback.token == token))).scalar_one_or_none()
    if not fb:
        raise HTTPException(404, "Feedback link not found")
    s = (await db.execute(select(FeedbackSettings).where(FeedbackSettings.org_id == fb.org_id))).scalar_one_or_none()
    return {
        "system": fb.system,
        "already_submitted": fb.submitted_at is not None,
        "rating": fb.rating,
        "heading": s.heading if s else "How did we do?",
        "subheading": s.subheading if s else None,
        "question": s.question if s else "How would you rate your experience?",
        "clarify_question": s.clarify_question if s else "Tell us more",
        "bands": (s.bands if s else {}) or {},
    }


class SubmitIn(BaseModel):
    rating: int
    comment: Optional[str] = None


def _band_message(system: str, rating: int, bands: dict) -> str:
    """Return the answer-to-score message for the rating band (Orderry)."""
    if not bands:
        return "Thank you for your feedback!"
    if system == "nps":
        if rating <= 6: return bands.get("low", "Thanks — we'll be in touch to improve.")
        if rating <= 8: return bands.get("mid", "Thanks! What could we improve?")
        return bands.get("high", "Thank you! We'd love a public review.")
    if system == "five_point":
        if rating <= 3: return bands.get("low", "Sorry to hear that — we'll reach out.")
        if rating == 4: return bands.get("mid", "Thanks! How can we earn a 5?")
        return bands.get("high", "Thank you! We'd love a public review.")
    # like/dislike
    return bands.get("high", "Thanks for your feedback!") if rating >= 1 else bands.get("low", "Sorry — we'll do better.")


@router.post("/public/{token}")
async def submit_feedback(token: str, data: SubmitIn, db: AsyncSession = Depends(get_db)):
    fb = (await db.execute(select(Feedback).where(Feedback.token == token))).scalar_one_or_none()
    if not fb:
        raise HTTPException(404, "Feedback link not found")
    if fb.submitted_at:
        raise HTTPException(409, "Feedback already submitted")
    fb.rating = data.rating
    fb.comment = data.comment
    fb.submitted_at = now_utc()
    s = (await db.execute(select(FeedbackSettings).where(FeedbackSettings.org_id == fb.org_id))).scalar_one_or_none()
    from backend.routers.feed import log_system_event
    await log_system_event(db, fb.org_id, "order", fb.order_id,
                           f"Customer left feedback: {data.rating}{' — ' + data.comment if data.comment else ''}")
    await db.commit()
    return {"ok": True, "message": _band_message(fb.system, data.rating, (s.bands if s else {}) or {})}


# ── Build 81: feedback report ────────────────────────────────────────────────
@router.get("/report")
async def feedback_report(
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    """Aggregate the feedback that has actually been submitted.

    Reports the average rating, the score distribution, and — for NPS — the real NPS
    figure (%promoters − %detractors), which is not the same thing as an average and
    can't be derived from one.
    """
    from datetime import datetime as _dtm

    q = select(Feedback).where(Feedback.org_id == current.org_id,
                               Feedback.submitted_at.isnot(None))
    if date_from:
        q = q.where(Feedback.submitted_at >= _dtm.combine(
            parse_date(date_from, "date_from"), _dtm.min.time()))
    if date_to:
        q = q.where(Feedback.submitted_at <= _dtm.combine(
            parse_date(date_to, "date_to"), _dtm.max.time()))
    rows = (await db.execute(q)).scalars().all()

    total = len(rows)
    if not total:
        return {"total": 0, "average": None, "nps": None, "distribution": {},
                "promoters": 0, "passives": 0, "detractors": 0, "with_comment": 0}

    ratings = [r.rating for r in rows if r.rating is not None]
    avg = q2(sum(ratings) / len(ratings)) if ratings else None

    distribution: dict = {}
    for r in ratings:
        distribution[str(r)] = distribution.get(str(r), 0) + 1

    # NPS is only meaningful for 0–10 responses
    nps_rows = [r.rating for r in rows if r.system == "nps" and r.rating is not None]
    promoters = sum(1 for x in nps_rows if x >= 9)
    passives = sum(1 for x in nps_rows if 7 <= x <= 8)
    detractors = sum(1 for x in nps_rows if x <= 6)
    nps = None
    if nps_rows:
        nps = round((promoters - detractors) / len(nps_rows) * 100)

    return {
        "total": total,
        "average": avg,
        "nps": nps,
        "distribution": distribution,
        "promoters": promoters,
        "passives": passives,
        "detractors": detractors,
        "with_comment": sum(1 for r in rows if r.comment),
    }
