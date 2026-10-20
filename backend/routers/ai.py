"""AI endpoints (Phase 3B). Each returns {provider: anthropic|stub, ...} so the
UI can show whether a live model or the deterministic fallback produced it."""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select, desc
from sqlalchemy.ext.asyncio import AsyncSession

from backend.db.database import get_db
from backend.models import Employee, Order, EventFeedItem, ChatThread, ChatMessage
from backend.services.auth import get_current_employee, record_scope
from backend.services import ai

async def ai_quota(current: Employee = Depends(get_current_employee)) -> None:
    """Every AI call runs on the platform's Anthropic key, so each person and each shop gets a
    budget: a burst limit per user and a daily ceiling per shop."""
    from backend.services.ratelimit import aenforce
    await aenforce("ai_user", current.id, 20, 60)
    await aenforce("ai_org", current.org_id, 1000, 86400)


router = APIRouter(prefix="/api/ai", tags=["ai"],
                   dependencies=[Depends(record_scope), Depends(ai_quota)])   # record scope on {order_id}/{client_id}


@router.get("/status")
async def status(current: Employee = Depends(get_current_employee)):
    return {"provider": ai.provider(), "live": ai.is_live(), "model": ai.settings.AI_MODEL}


@router.post("/orders/{order_id}/summary")
async def order_summary(order_id: str, db: AsyncSession = Depends(get_db),
                        current: Employee = Depends(get_current_employee)):
    o = (await db.execute(select(Order).where(
        Order.id == order_id, Order.org_id == current.org_id))).scalar_one_or_none()
    if not o:
        raise HTTPException(404, "Order not found")
    rows = (await db.execute(select(EventFeedItem).where(
        EventFeedItem.org_id == current.org_id, EventFeedItem.entity_type == "order",
        EventFeedItem.entity_id == order_id).order_by(EventFeedItem.created_at).limit(40))).scalars().all()
    events = [r.body for r in rows if r.body]
    return await ai.summarize_order(order_number=o.order_number, complaint=o.complaint or "",
                                    status=o.status or "new", events=events)


class DraftIn(BaseModel):
    complaint: str = Field(max_length=4000)
    device: Optional[str] = ""


@router.post("/estimate/draft")
async def estimate_draft(data: DraftIn, current: Employee = Depends(get_current_employee)):
    if not data.complaint.strip():
        raise HTTPException(400, "complaint is required")
    return await ai.draft_estimate_lines(complaint=data.complaint, device=data.device or "")


@router.post("/chat/{thread_id}/suggest")
async def chat_suggest(thread_id: str, db: AsyncSession = Depends(get_db),
                       current: Employee = Depends(get_current_employee)):
    t = (await db.execute(select(ChatThread).where(
        ChatThread.id == thread_id, ChatThread.org_id == current.org_id))).scalar_one_or_none()
    if not t:
        raise HTTPException(404, "Thread not found")
    last_in = (await db.execute(select(ChatMessage).where(
        ChatMessage.thread_id == thread_id, ChatMessage.direction == "in")
        .order_by(desc(ChatMessage.created_at)))).scalars().first()
    if not last_in:
        raise HTTPException(400, "No inbound message to reply to")
    return await ai.suggest_reply(customer_message=last_in.body or "",
                                  context=f"Channel {t.channel}, contact {t.contact_name or t.contact_phone or ''}")


class ProductDescIn(BaseModel):
    name: str = Field(max_length=300)
    brand: str = Field("", max_length=200)
    category: str = Field("", max_length=200)
    specs: str = Field("", max_length=4000)


@router.post("/product-description")
async def product_description(data: ProductDescIn,
                              current: Employee = Depends(get_current_employee)):
    """A5: draft a catalog product description."""
    return await ai.draft_product_description(
        name=data.name, brand=data.brand, category=data.category, specs=data.specs)


class RecognizeIn(BaseModel):
    text: str = Field(max_length=8000)


@router.post("/recognize-items")
async def recognize_items(data: RecognizeIn,
                          current: Employee = Depends(get_current_employee)):
    """A5: extract structured line-items from free text (parts list / dictation)."""
    return await ai.recognize_items(text=data.text)


class TranscribeIn(BaseModel):
    audio_present: bool = False
    hint: str = Field("", max_length=1000)


@router.post("/transcribe")
async def transcribe(data: TranscribeIn,
                     current: Employee = Depends(get_current_employee)):
    """A5: transcription seam (gated on a configured speech provider)."""
    return await ai.transcribe_audio(audio_present=data.audio_present, hint=data.hint)


class RemoveBgIn(BaseModel):
    image_present: bool = False


@router.post("/remove-background")
async def remove_background(data: RemoveBgIn,
                            current: Employee = Depends(get_current_employee)):
    """A5: product-photo background removal seam (gated on an image provider)."""
    return await ai.remove_background(image_present=data.image_present)
