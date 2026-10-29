"""Memberships API (docs/SPEC_memberships.md §12): plans, selling, the list staff work from,
what a client / asset / work order has, changes, settings and the profitability report."""
from datetime import date, timedelta
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.db.database import get_db
from backend.models import (
    Client,
    Device,
    Employee,
    Membership,
    MembershipAsset,
    MembershipPeriod,
    MembershipPlan,
    MembershipPlanItem,
    MembershipUsage,
    Order,
    Organisation,
    Service,
)
from backend.services import memberships as svc
from backend.services.auth import get_current_employee, has_perm, require_perm

router = APIRouter(prefix="/api/memberships", tags=["memberships"])


# ── shapes ───────────────────────────────────────────────────────────────────────────────────────

class ItemIn(BaseModel):
    kind: Literal["service", "discount", "perk"] = "service"
    service_id: Optional[str] = None
    label: str = Field("", max_length=200)
    qty: int = Field(1, ge=0, le=999)
    pct: float = Field(0, ge=0, le=100)
    applies_to: Literal["labour", "parts", "both"] = "labour"
    window_start: Optional[str] = Field(None, max_length=5)
    window_end: Optional[str] = Field(None, max_length=5)
    rollover: bool = False


class PlanIn(BaseModel):
    name: str = Field(..., max_length=120)
    description: str = Field("", max_length=2000)
    template_key: Optional[str] = Field(None, max_length=30)
    asset_group_ids: list[str] = []
    price_monthly: Optional[float] = Field(None, ge=0)
    price_yearly: Optional[float] = Field(None, ge=0)
    term: Literal["rolling", "season"] = "rolling"
    season_start: Optional[str] = Field(None, max_length=5)
    season_end: Optional[str] = Field(None, max_length=5)
    presale_days: int = Field(60, ge=0, le=366)
    late_join: Literal["prorated", "full"] = "prorated"
    color: str = Field("#0059ba", max_length=20)
    active: bool = True
    items: list[ItemIn] = []


class SellIn(BaseModel):
    plan_id: str
    client_id: str
    device_id: Optional[str] = None
    billing: Literal["monthly", "yearly"] = "yearly"
    order_id: Optional[str] = None
    manager_id: Optional[str] = None


class CancelIn(BaseModel):
    when: Literal["now", "period_end"] = "period_end"


class BillingIn(BaseModel):
    billing: Literal["monthly", "yearly"]


class TransferIn(BaseModel):
    device_id: str
    client_id: Optional[str] = None      # set when the asset was sold to someone else


class SettingsIn(BaseModel):
    membership_spread_revenue: Optional[bool] = None
    membership_grace_days: Optional[int] = Field(None, ge=0, le=90)
    membership_lapse_days: Optional[int] = Field(None, ge=1, le=365)
    membership_renewal_notice_days: Optional[int] = Field(None, ge=0, le=120)
    membership_checkout_prompt: Optional[bool] = None
    membership_prompt_threshold_pct: Optional[float] = Field(None, ge=0, le=100)
    membership_prompt_snooze_days: Optional[int] = Field(None, ge=0, le=365)


SETTINGS = list(SettingsIn.model_fields)


# ── helpers ──────────────────────────────────────────────────────────────────────────────────────

async def _plan(db: AsyncSession, org_id: str, pid: str) -> MembershipPlan:
    p = (await db.execute(select(MembershipPlan).where(
        MembershipPlan.id == pid, MembershipPlan.org_id == org_id))).scalar_one_or_none()
    if not p:
        raise HTTPException(404, "Plan not found")
    return p


async def _membership(db: AsyncSession, org_id: str, mid: str) -> Membership:
    m = (await db.execute(select(Membership).where(
        Membership.id == mid, Membership.org_id == org_id))).scalar_one_or_none()
    if not m:
        raise HTTPException(404, "Membership not found")
    return m


async def _service_names(db: AsyncSession, org_id: str, ids: list[str]) -> dict:
    if not ids:
        return {}
    return dict((await db.execute(select(Service.id, Service.name).where(
        Service.org_id == org_id, Service.id.in_(ids)))).all())


async def _plan_out(db: AsyncSession, p: MembershipPlan) -> dict:
    items = await svc.plan_items(db, p.id)
    names = await _service_names(db, p.org_id, [i.service_id for i in items if i.service_id])
    prices = dict((await db.execute(select(Service.id, Service.price).where(
        Service.id.in_([i.service_id for i in items if i.service_id] or ["-"])))).all())
    members = (await db.execute(select(func.count()).select_from(Membership).where(
        Membership.plan_id == p.id, Membership.status.in_(svc.LIVE)))).scalar_one()
    return {
        "id": p.id, "name": p.name, "description": p.description, "template_key": p.template_key,
        "asset_group_ids": p.asset_group_ids or [], "price_monthly": p.price_monthly,
        "price_yearly": p.price_yearly, "term": p.term, "season_start": p.season_start,
        "season_end": p.season_end, "presale_days": p.presale_days, "late_join": p.late_join,
        "color": p.color, "active": p.active, "members": members,
        # What the included services would cost a non-member over a year (the plan's "worth").
        "value_yearly": round(sum(float(prices.get(i.service_id) or 0) * (i.qty or 0)
                                  for i in items if i.kind == "service"), 2),
        "items": [{"id": i.id, "kind": i.kind, "service_id": i.service_id, "label": i.label,
                   "service_name": names.get(i.service_id), "qty": i.qty, "pct": i.pct,
                   "applies_to": i.applies_to, "window_start": i.window_start,
                   "window_end": i.window_end, "rollover": i.rollover,
                   "text": svc.label_of(i, names.get(i.service_id))} for i in items],
    }


def _asset_label(d: Optional[Device]) -> str:
    if not d:
        return ""
    return " ".join(x for x in [d.brand, d.model] if x) or (d.serial or "Asset")


async def _membership_out(db: AsyncSession, m: Membership, *, detail: bool = False,
                          show_money: bool = True) -> dict:
    plan = (await db.execute(select(MembershipPlan).where(MembershipPlan.id == m.plan_id))).scalar_one()
    client = (await db.execute(select(Client).where(Client.id == m.client_id))).scalar_one_or_none()
    device = (await db.execute(select(Device).where(Device.id == m.device_id))).scalar_one_or_none() \
        if m.device_id else None
    unpaid = (await db.execute(select(func.coalesce(func.sum(MembershipPeriod.amount), 0)).where(
        MembershipPeriod.membership_id == m.id, MembershipPeriod.paid_at.is_(None),
        MembershipPeriod.due_on <= date.today()))).scalar_one() or 0
    saved = (await db.execute(select(func.coalesce(func.sum(MembershipUsage.value), 0)).where(
        MembershipUsage.membership_id == m.id, MembershipUsage.term_start == m.term_start))).scalar_one() or 0
    oldest_due = (await db.execute(select(func.min(MembershipPeriod.due_on)).where(
        MembershipPeriod.membership_id == m.id, MembershipPeriod.paid_at.is_(None),
        MembershipPeriod.due_on <= date.today()))).scalar_one()
    row = {
        "id": m.id, "plan_id": plan.id, "plan": plan.name, "color": plan.color,
        "client_id": m.client_id, "client": client.name if client else "",
        "device_id": m.device_id, "asset": _asset_label(device), "billing": m.billing,
        "status": m.status, "renews": m.renews, "renews_on": m.term_end.isoformat(),
        "starts_on": m.term_start.isoformat(), "period_end": m.period_end.isoformat(),
        "ended_on": m.ended_on.isoformat() if m.ended_on else None, "ended_reason": m.ended_reason,
        "manager_id": m.manager_id, "unpaid": bool(unpaid),
        "attention": None,
    }
    if show_money:
        row["saved"] = float(saved)
    if m.status in svc.LIVE and oldest_due and unpaid:
        days = (date.today() - oldest_due).days
        row["attention"] = {"kind": "unpaid", "text": "Payment due today" if days == 0 else f"Unpaid {days} day{'s' if days != 1 else ''}"}
    if show_money:
        row.update({"price": m.price, "owed": float(unpaid)})
    if detail or m.status in svc.LIVE:
        items = []
        names = None
        for it in await svc.plan_items(db, plan.id):
            if names is None:
                names = await _service_names(db, m.org_id, [i.service_id for i in await svc.plan_items(db, plan.id)
                                                            if i.service_id])
            entry = {"id": it.id, "kind": it.kind, "text": svc.label_of(it, names.get(it.service_id))}
            if it.kind == "service":
                entry["left"] = await svc.remaining(db, m, it)
                entry["of"] = await svc.allowance(db, m, it)
                win = svc.window_around(it, m.term_start)
                if win:
                    entry["window"] = [win[0].isoformat(), (win[1] - timedelta(days=1)).isoformat()]
                    closes = win[1] - timedelta(days=1)
                    if row["attention"] is None and entry["left"] > 0 and win[0] <= date.today() <= closes \
                            and (closes - date.today()).days <= 30:
                        row["attention"] = {"kind": "window",
                                            "text": f"{entry['text'].split(' × ')[0]} not booked · closes {closes:%d %b}"}
            items.append(entry)
        row["items"] = items
    if detail and show_money:
        given = (await db.execute(select(func.coalesce(func.sum(MembershipUsage.value), 0)).where(
            MembershipUsage.membership_id == m.id))).scalar_one() or 0
        paid = (await db.execute(select(func.coalesce(func.sum(MembershipPeriod.amount), 0)).where(
            MembershipPeriod.membership_id == m.id, MembershipPeriod.paid_at.isnot(None)))).scalar_one() or 0
        row.update({"value_given": float(given), "paid": float(paid)})
        periods = (await db.execute(select(MembershipPeriod).where(
            MembershipPeriod.membership_id == m.id).order_by(MembershipPeriod.period_start.desc()))).scalars().all()
        row["periods"] = [{"id": p.id, "start": p.period_start.isoformat(), "end": p.period_end.isoformat(),
                           "amount": p.amount, "paid": bool(p.paid_at), "invoice_id": p.invoice_id,
                           "order_id": p.order_id, "due_on": p.due_on.isoformat()} for p in periods[:24]]
    return row


# ── plans ────────────────────────────────────────────────────────────────────────────────────────

@router.get("/plans")
async def list_plans(db: AsyncSession = Depends(get_db), current: Employee = Depends(get_current_employee)):
    plans = (await db.execute(select(MembershipPlan).where(
        MembershipPlan.org_id == current.org_id).order_by(MembershipPlan.active.desc(),
                                                            MembershipPlan.name))).scalars().all()
    return {"items": [await _plan_out(db, p) for p in plans]}


@router.get("/plans/starters")
async def starter_plans(template: str = Query(..., max_length=30), db: AsyncSession = Depends(get_db),
                        current: Employee = Depends(require_perm("can_manage_membership_plans"))):
    return {"items": await svc.starter(db, current.org_id, template)}


async def _save_plan(db: AsyncSession, org_id: str, p: MembershipPlan, data: PlanIn) -> None:
    for k in ("name", "description", "template_key", "asset_group_ids", "price_monthly", "price_yearly",
              "term", "season_start", "season_end", "presale_days", "late_join", "color", "active"):
        setattr(p, k, getattr(data, k))
    p.name = (p.name or "").strip()
    p.price_monthly = p.price_monthly or None
    p.price_yearly = p.price_yearly or None
    if p.term != "season":
        p.season_start = p.season_end = None
    svc.validate_plan(p)
    names = await _service_names(db, org_id, [i.service_id for i in data.items if i.service_id])
    for i in data.items:
        if i.service_id and i.service_id not in names:
            raise HTTPException(404, "One of the included services isn't in your catalog")
    old = (await db.execute(select(MembershipPlanItem).where(MembershipPlanItem.plan_id == p.id))).scalars().all()
    used = set((await db.execute(select(MembershipUsage.plan_item_id).where(
        MembershipUsage.plan_item_id.in_([o.id for o in old])))).scalars().all()) if old else set()
    for o in old:
        if o.id in used:
            o.sort_order = -1       # keep history; hidden from the plan (sort_order < 0)
            o.qty = 0
            o.kind = "perk" if o.kind != "perk" else o.kind
        else:
            await db.delete(o)
    await db.flush()
    for n, i in enumerate(data.items):
        it = MembershipPlanItem(org_id=org_id, plan_id=p.id, kind=i.kind, service_id=i.service_id if i.kind == "service" else None,
                                label=(i.label or names.get(i.service_id) or "").strip(), qty=i.qty if i.kind == "service" else 0,
                                pct=i.pct if i.kind == "discount" else 0, applies_to=i.applies_to,
                                window_start=i.window_start or None, window_end=i.window_end or None,
                                rollover=i.rollover, sort_order=n)
        svc.validate_item(it)
        db.add(it)


@router.post("/plans", status_code=201)
async def create_plan(data: PlanIn, db: AsyncSession = Depends(get_db),
                      current: Employee = Depends(require_perm("can_manage_membership_plans"))):
    p = MembershipPlan(org_id=current.org_id, name=data.name)
    db.add(p)
    await db.flush()
    await _save_plan(db, current.org_id, p, data)
    await db.commit()
    return await _plan_out(db, p)


@router.put("/plans/{pid}")
async def update_plan(pid: str, data: PlanIn, db: AsyncSession = Depends(get_db),
                      current: Employee = Depends(require_perm("can_manage_membership_plans"))):
    p = await _plan(db, current.org_id, pid)
    await _save_plan(db, current.org_id, p, data)
    await db.commit()
    return await _plan_out(db, p)


# ── memberships ──────────────────────────────────────────────────────────────────────────────────

@router.get("")
async def list_memberships(
    view: Literal["all", "renewing", "unpaid", "paused", "former"] = "all",
    plan_id: Optional[str] = None, manager_id: Optional[str] = None, q: Optional[str] = Query(None, max_length=100),
    db: AsyncSession = Depends(get_db), current: Employee = Depends(get_current_employee),
):
    await svc.sync_payments(db, current.org_id)
    await db.commit()
    stmt = select(Membership).where(Membership.org_id == current.org_id)
    today = date.today()
    if view == "former":
        stmt = stmt.where(Membership.status.in_(svc.ENDED))
    else:
        stmt = stmt.where(Membership.status.in_(svc.LIVE))
    if view == "renewing":
        stmt = stmt.where(Membership.term_end <= today + timedelta(days=30), Membership.renews == True)  # noqa: E712
    if view == "paused":
        stmt = stmt.where(Membership.status == "paused")
    if view == "unpaid":
        stmt = stmt.where(select(MembershipPeriod.id).where(
            MembershipPeriod.membership_id == Membership.id, MembershipPeriod.paid_at.is_(None),
            MembershipPeriod.due_on <= today).exists())
    if plan_id:
        stmt = stmt.where(Membership.plan_id == plan_id)
    if manager_id:
        stmt = stmt.where(Membership.manager_id == manager_id)
    if q:
        stmt = stmt.join(Client, Client.id == Membership.client_id).where(Client.name.ilike(f"%{q.strip()}%"))
    rows = (await db.execute(stmt.order_by(Membership.term_end).limit(500))).scalars().all()
    money = await has_perm(db, current, "can_view_client_info")
    counts = {}
    for k, cond in (("all", Membership.status.in_(svc.LIVE)), ("paused", Membership.status == "paused"),
                    ("former", Membership.status.in_(svc.ENDED))):
        counts[k] = (await db.execute(select(func.count()).select_from(Membership).where(
            Membership.org_id == current.org_id, cond))).scalar_one()
    counts["renewing"] = (await db.execute(select(func.count()).select_from(Membership).where(
        Membership.org_id == current.org_id, Membership.status.in_(svc.LIVE), Membership.renews == True,  # noqa: E712
        Membership.term_end <= today + timedelta(days=30)))).scalar_one()
    counts["unpaid"] = (await db.execute(select(func.count(func.distinct(MembershipPeriod.membership_id))).join(
        Membership, Membership.id == MembershipPeriod.membership_id).where(
        Membership.org_id == current.org_id, Membership.status.in_(svc.LIVE),
        MembershipPeriod.paid_at.is_(None), MembershipPeriod.due_on <= today))).scalar_one()
    totals = None
    if money:
        live = (await db.execute(select(Membership.billing, Membership.price, Membership.status, Membership.term_end,
                                        Membership.renews).where(
            Membership.org_id == current.org_id, Membership.status.in_(svc.LIVE)))).all()
        owed = (await db.execute(select(func.coalesce(func.sum(MembershipPeriod.amount), 0)).join(
            Membership, Membership.id == MembershipPeriod.membership_id).where(
            Membership.org_id == current.org_id, Membership.status.in_(svc.LIVE),
            MembershipPeriod.paid_at.is_(None), MembershipPeriod.due_on <= today))).scalar_one() or 0
        totals = {
            # Monthly recurring revenue: active plans only; a yearly plan counts a twelfth.
            "mrr": round(sum(float(p or 0) / (12 if b == "yearly" else 1) for b, p, st, _, _ in live if st == "active"), 2),
            "renewing_value": round(sum(float(p or 0) for b, p, st, end, rn in live
                                        if rn and end <= today + timedelta(days=30)), 2),
            "owed": float(owed),
        }
    return {"items": [await _membership_out(db, m, show_money=money) for m in rows], "counts": counts, "totals": totals}


@router.get("/for")
async def memberships_for(client_id: Optional[str] = None, device_id: Optional[str] = None,
                          db: AsyncSession = Depends(get_db), current: Employee = Depends(get_current_employee)):
    """What a client (profile card) or an asset (asset page) has: live plans and former ones."""
    if not client_id and not device_id:
        raise HTTPException(422, "Pass a client or an asset")
    stmt = select(Membership).where(Membership.org_id == current.org_id)
    if client_id:
        stmt = stmt.where(Membership.client_id == client_id)
    if device_id:
        ids = (await db.execute(select(MembershipAsset.membership_id).where(
            MembershipAsset.org_id == current.org_id, MembershipAsset.device_id == device_id))).scalars().all()
        stmt = stmt.where(Membership.id.in_(ids or ["-"]))
    await svc.sync_payments(db, current.org_id)
    await db.commit()
    rows = (await db.execute(stmt.order_by(Membership.created_at.desc()))).scalars().all()
    money = await has_perm(db, current, "can_view_client_info")
    live = [await _membership_out(db, m, show_money=money) for m in rows if m.status in svc.LIVE]
    former = []
    seen = {m["plan_id"] for m in live}
    for m in rows:
        if m.status in svc.ENDED and m.plan_id not in seen:
            seen.add(m.plan_id)
            former.append(await _membership_out(db, m, show_money=money))
    return {"live": live, "former": former}


@router.get("/order/{order_id}")
async def for_order(order_id: str, db: AsyncSession = Depends(get_db),
                    current: Employee = Depends(get_current_employee)):
    """The work order's plan banner, lines a plan could still cover, and the checkout offer."""
    order = (await db.execute(select(Order).where(
        Order.id == order_id, Order.org_id == current.org_id))).scalar_one_or_none()
    if not order:
        raise HTTPException(404, "Order not found")
    plans = []
    if order.device_id:
        ids = (await db.execute(select(MembershipAsset.membership_id).where(
            MembershipAsset.device_id == order.device_id))).scalars().all()
        ms = (await db.execute(select(Membership).where(
            Membership.id.in_(ids or ["-"]), Membership.status.in_(svc.LIVE)))).scalars().all()
        plans = [await _membership_out(db, m, show_money=await has_perm(db, current, "can_view_client_info"))
                 for m in ms]
    coverable = 0
    if plans and order.device_id:
        from backend.models import OrderLine
        ms = await svc.live_on_device(db, current.org_id, order.device_id)
        for line in (await db.execute(select(OrderLine).where(OrderLine.order_id == order.id))).scalars().all():
            if await svc.coverage_for_line(db, ms, line):
                coverable += 1
    offer = None
    if not plans and await has_perm(db, current, "can_sell_memberships"):
        org = (await db.execute(select(Organisation).where(Organisation.id == current.org_id))).scalar_one()
        offer = await svc.offer_for_order(db, org, order)
        if offer:
            offer["order_total"] = float(order.total or 0)
    return {"plans": plans, "coverable": coverable, "client_id": order.client_id, "device_id": order.device_id,
            "can_apply": await has_perm(db, current, "can_apply_membership_benefits"), "offer": offer}


@router.post("/order/{order_id}/apply")
async def apply_on_order(order_id: str, db: AsyncSession = Depends(get_db),
                         current: Employee = Depends(require_perm("can_apply_membership_benefits"))):
    order = (await db.execute(select(Order).where(
        Order.id == order_id, Order.org_id == current.org_id))).scalar_one_or_none()
    if not order:
        raise HTTPException(404, "Order not found")
    n = await svc.apply_to_order(db, order)
    if n:
        await _retotal(db, order)
    await db.commit()
    return {"applied": n}


async def _retotal(db: AsyncSession, order: Order) -> None:
    from backend.models import OrderLine
    from backend.routers.orders import _calc_line, _recalc_order
    for line in (await db.execute(select(OrderLine).where(
            OrderLine.order_id == order.id, OrderLine.membership_id.isnot(None)))).scalars().all():
        line.total = _calc_line(line)
    await _recalc_order(db, order)


@router.post("/order/{order_id}/snooze")
async def snooze_offer(order_id: str, db: AsyncSession = Depends(get_db),
                       current: Employee = Depends(require_perm("can_sell_memberships"))):
    """"Not now" on the checkout offer: hide it for this client for the shop's snooze period."""
    order = (await db.execute(select(Order).where(
        Order.id == order_id, Order.org_id == current.org_id))).scalar_one_or_none()
    if not order or not order.client_id:
        raise HTTPException(404, "Order not found")
    org = (await db.execute(select(Organisation).where(Organisation.id == current.org_id))).scalar_one()
    client = (await db.execute(select(Client).where(Client.id == order.client_id))).scalar_one()
    client.membership_offer_snoozed_until = date.today() + timedelta(days=org.membership_prompt_snooze_days or 0)
    await db.commit()
    return {"ok": True}


@router.post("", status_code=201)
async def sell_membership(data: SellIn, db: AsyncSession = Depends(get_db),
                          current: Employee = Depends(require_perm("can_sell_memberships"))):
    m = await svc.sell(db, org_id=current.org_id, actor=current, plan_id=data.plan_id,
                       client_id=data.client_id, device_id=data.device_id, billing=data.billing,
                       order_id=data.order_id, manager_id=data.manager_id)
    if data.order_id:
        order = (await db.execute(select(Order).where(Order.id == data.order_id))).scalar_one()
        await _retotal(db, order)
    from backend.routers.feed import log_system_event
    plan = await _plan(db, current.org_id, m.plan_id)
    await log_system_event(db, current.org_id, "client", m.client_id, f"Joined {plan.name} ({m.billing})")
    await db.commit()
    period = (await db.execute(select(MembershipPeriod).where(
        MembershipPeriod.membership_id == m.id))).scalars().first()
    return {**await _membership_out(db, m, detail=True), "invoice_id": period.invoice_id if period else None}


@router.get("/{mid}")
async def get_membership(mid: str, db: AsyncSession = Depends(get_db),
                         current: Employee = Depends(get_current_employee)):
    m = await _membership(db, current.org_id, mid)
    await svc.sync_payments(db, current.org_id, m.id)
    await db.commit()
    money = await has_perm(db, current, "can_view_client_info")
    out = await _membership_out(db, m, detail=True, show_money=money)
    if money and m.status in svc.LIVE:
        used = float(out.get("value_given") or 0)
        paid = float((await db.execute(select(func.coalesce(func.sum(MembershipPeriod.amount), 0)).where(
            MembershipPeriod.membership_id == m.id, MembershipPeriod.paid_at.isnot(None),
            MembershipPeriod.period_start == m.period_start))).scalar_one() or 0)
        out["refund_if_cancelled_now"] = svc.refund_estimate(m, paid, used, date.today())
    return out


@router.post("/{mid}/cancel")
async def cancel_membership(mid: str, data: CancelIn, db: AsyncSession = Depends(get_db),
                            current: Employee = Depends(require_perm("can_change_memberships"))):
    m = await _membership(db, current.org_id, mid)
    if m.status not in svc.LIVE:
        raise HTTPException(409, "This membership has already ended")
    await svc.sync_payments(db, current.org_id, m.id)
    refund = 0.0
    if data.when == "now":
        given = float((await db.execute(select(func.coalesce(func.sum(MembershipUsage.value), 0)).where(
            MembershipUsage.membership_id == m.id, MembershipUsage.term_start == m.term_start))).scalar_one() or 0)
        paid = float((await db.execute(select(func.coalesce(func.sum(MembershipPeriod.amount), 0)).where(
            MembershipPeriod.membership_id == m.id, MembershipPeriod.paid_at.isnot(None),
            MembershipPeriod.period_start == m.period_start))).scalar_one() or 0)
        refund = svc.refund_estimate(m, paid, given, date.today())
        # Unpaid future/current periods are dropped with the plan: void their invoices.
        from backend.models import Invoice
        for p in (await db.execute(select(MembershipPeriod).where(
                MembershipPeriod.membership_id == m.id, MembershipPeriod.paid_at.is_(None)))).scalars().all():
            if p.invoice_id:
                inv = (await db.execute(select(Invoice).where(Invoice.id == p.invoice_id))).scalar_one_or_none()
                if inv and (inv.amount_paid or 0) == 0:
                    inv.status = "cancelled"
        await svc.end(db, m, "cancelled")
    else:
        m.renews = False
        m.ended_reason = "cancelled"
    from backend.routers.feed import log_system_event
    plan = await _plan(db, current.org_id, m.plan_id)
    await log_system_event(db, current.org_id, "client", m.client_id,
                           f"{plan.name} cancelled " + ("now" if data.when == "now" else "at period end"))
    await db.commit()
    return {**await _membership_out(db, m, detail=True), "refund_suggested": refund}


@router.post("/{mid}/resume")
async def resume_membership(mid: str, db: AsyncSession = Depends(get_db),
                            current: Employee = Depends(require_perm("can_change_memberships"))):
    """Undo "cancel at period end", or lift a manual pause."""
    m = await _membership(db, current.org_id, mid)
    if m.status not in svc.LIVE:
        raise HTTPException(409, "This membership has ended. Sell the plan again to re-join.")
    m.renews = True
    m.ended_reason = None
    if m.status == "paused" and not await svc._unpaid_started(db, m, date.today()):
        m.status = "active"
    await db.commit()
    return await _membership_out(db, m, detail=True)


@router.post("/{mid}/pause")
async def pause_membership(mid: str, db: AsyncSession = Depends(get_db),
                           current: Employee = Depends(require_perm("can_change_memberships"))):
    m = await _membership(db, current.org_id, mid)
    if m.status != "active":
        raise HTTPException(409, "Only an active membership can be paused")
    m.status = "paused"
    await db.commit()
    return await _membership_out(db, m, detail=True)


@router.post("/{mid}/billing")
async def switch_billing(mid: str, data: BillingIn, db: AsyncSession = Depends(get_db),
                         current: Employee = Depends(require_perm("can_change_memberships"))):
    """Monthly ↔ yearly, from the next payment on (the current paid period runs out first)."""
    m = await _membership(db, current.org_id, mid)
    plan = await _plan(db, current.org_id, m.plan_id)
    price = svc.plan_offers(plan, data.billing)
    if not price:
        raise HTTPException(422, f"{plan.name} isn't sold {data.billing}")
    if m.status not in svc.LIVE:
        raise HTTPException(409, "This membership has ended")
    # Drop a not-yet-paid upcoming period so the next one is created with the new billing.
    for p in (await db.execute(select(MembershipPeriod).where(
            MembershipPeriod.membership_id == m.id, MembershipPeriod.paid_at.is_(None),
            MembershipPeriod.period_start >= m.period_end))).scalars().all():
        from backend.models import Invoice
        if p.invoice_id:
            inv = (await db.execute(select(Invoice).where(Invoice.id == p.invoice_id))).scalar_one_or_none()
            if inv and (inv.amount_paid or 0) == 0:
                inv.status = "cancelled"
        await db.delete(p)
    m.billing, m.price = data.billing, price
    await db.commit()
    return await _membership_out(db, m, detail=True)


@router.post("/{mid}/transfer")
async def transfer_membership(mid: str, data: TransferIn, db: AsyncSession = Depends(get_db),
                              current: Employee = Depends(require_perm("can_change_memberships"))):
    """Move the plan to another asset (a replaced car), or with the asset to its new owner."""
    m = await _membership(db, current.org_id, mid)
    if m.status not in svc.LIVE:
        raise HTTPException(409, "This membership has ended")
    device = (await db.execute(select(Device).where(
        Device.id == data.device_id, Device.org_id == current.org_id))).scalar_one_or_none()
    if not device:
        raise HTTPException(404, "Asset not found")
    new_client = data.client_id or m.client_id
    if not (await db.execute(select(Client.id).where(
            Client.id == new_client, Client.org_id == current.org_id))).scalar_one_or_none():
        raise HTTPException(404, "Client not found")
    if device.client_id and device.client_id != new_client:
        raise HTTPException(409, "That asset belongs to another client")
    plan = await _plan(db, current.org_id, m.plan_id)
    if not svc.eligible(plan, device):
        raise HTTPException(409, f"{plan.name} isn't for this kind of asset")
    others = [x for x in await svc.live_on_device(db, current.org_id, device.id)
              if x.id != m.id and x.plan_id == m.plan_id]
    if others:
        raise HTTPException(409, f"That asset already has {plan.name}")
    old_client = m.client_id
    for row in (await db.execute(select(MembershipAsset).where(MembershipAsset.membership_id == m.id))).scalars().all():
        await db.delete(row)
    await db.flush()
    db.add(MembershipAsset(org_id=current.org_id, membership_id=m.id, device_id=device.id))
    m.device_id, m.client_id = device.id, new_client
    await db.flush()
    await svc.sync_tags(db, current.org_id, old_client)
    if new_client != old_client:
        await svc.sync_tags(db, current.org_id, new_client)
    await db.commit()
    return await _membership_out(db, m, detail=True)


@router.post("/{mid}/pay-link")
async def membership_pay_link(mid: str, db: AsyncSession = Depends(get_db),
                              current: Employee = Depends(require_perm("can_create_payment_links"))):
    m = await _membership(db, current.org_id, mid)
    p = (await db.execute(select(MembershipPeriod).where(
        MembershipPeriod.membership_id == m.id, MembershipPeriod.paid_at.is_(None),
        MembershipPeriod.invoice_id.isnot(None)).order_by(MembershipPeriod.due_on))).scalars().first()
    if not p:
        raise HTTPException(409, "Nothing to pay on this membership")
    url = await svc.pay_link(db, p)
    await db.commit()
    if not url:
        raise HTTPException(409, "Online payments aren't set up. Take the payment on the invoice instead.")
    return {"url": url, "invoice_id": p.invoice_id}


# ── settings & report ────────────────────────────────────────────────────────────────────────────

@router.get("/settings/shop")
async def get_settings(db: AsyncSession = Depends(get_db), current: Employee = Depends(get_current_employee)):
    org = (await db.execute(select(Organisation).where(Organisation.id == current.org_id))).scalar_one()
    return {k: getattr(org, k) for k in SETTINGS}


@router.put("/settings/shop")
async def put_settings(data: SettingsIn, db: AsyncSession = Depends(get_db),
                       current: Employee = Depends(require_perm("can_manage_membership_plans"))):
    org = (await db.execute(select(Organisation).where(Organisation.id == current.org_id))).scalar_one()
    for k, v in data.model_dump(exclude_unset=True).items():
        if v is not None:
            setattr(org, k, v)
    if org.membership_lapse_days < org.membership_grace_days:
        raise HTTPException(422, "Ending a plan must come after pausing it")
    await db.commit()
    return {k: getattr(org, k) for k in SETTINGS}


@router.get("/reports/profitability")
async def report(start: Optional[date] = None, end: Optional[date] = None, db: AsyncSession = Depends(get_db),
                 current: Employee = Depends(require_perm("can_see_profit"))):
    end = end or date.today() + timedelta(days=1)
    start = start or end - timedelta(days=365)
    return await svc.profitability(db, current.org_id, start, end)
