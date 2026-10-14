"""AI endpoints (Phase 3B). Each returns {provider: anthropic|stub, ...} so the
UI can show whether a live model or the deterministic fallback produced it."""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select, desc
from sqlalchemy.ext.asyncio import AsyncSession

from backend.db.database import get_db
from backend.models import Employee, Order, EventFeedItem, ChatThread, ChatMessage
from backend.services.auth import get_current_employee
from backend.services import ai

router = APIRouter(prefix="/api/ai", tags=["ai"])


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
    complaint: str
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
    name: str
    brand: str = ""
    category: str = ""
    specs: str = ""


@router.post("/product-description")
async def product_description(data: ProductDescIn,
                              current: Employee = Depends(get_current_employee)):
    """A5: draft a catalog product description."""
    return await ai.draft_product_description(
        name=data.name, brand=data.brand, category=data.category, specs=data.specs)


class RecognizeIn(BaseModel):
    text: str


@router.post("/recognize-items")
async def recognize_items(data: RecognizeIn,
                          current: Employee = Depends(get_current_employee)):
    """A5: extract structured line-items from free text (parts list / dictation)."""
    return await ai.recognize_items(text=data.text)


class TranscribeIn(BaseModel):
    audio_present: bool = False
    hint: str = ""


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
