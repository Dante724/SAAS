"""Memberships (docs/SPEC_memberships.md): service plans on boats, vehicles and devices.

A plan is a list of included items (catalog services with a count per plan year, a discount, or a
perk), priced monthly and/or yearly. A membership puts one plan on one asset. Every charge is an
ordinary invoice (or a line on the work order it was sold on), so the books need nothing new.

Dates: every `*_end` here is exclusive — a plan year that starts on 12 Mar 2027 has term_end
12 Mar 2028, which is also the renewal date shown to people.
"""
from __future__ import annotations

import calendar
import hashlib
import logging
from datetime import date, datetime, timedelta
from typing import Optional

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import (
    Client,
    Device,
    Employee,
    Invoice,
    InvoiceLine,
    Membership,
    MembershipAsset,
    MembershipPeriod,
    MembershipPlan,
    MembershipPlanItem,
    MembershipUsage,
    NotificationLog,
    Order,
    OrderLine,
    Organisation,
    Service,
    Tag,
    Taggable,
)
from backend.services.money import q2

log = logging.getLogger("tekly.memberships")

LIVE = ("scheduled", "active", "paused")          # still a member
ENDED = ("lapsed", "cancelled")
BILLING = ("monthly", "yearly")
ITEM_KINDS = ("service", "discount", "perk")


# ── dates ────────────────────────────────────────────────────────────────────────────────────────

def add_months(d: date, n: int) -> date:
    m = d.month - 1 + n
    y, m = d.year + m // 12, m % 12 + 1
    return date(y, m, min(d.day, calendar.monthrange(y, m)[1]))


def _md(s: Optional[str]) -> tuple[int, int]:
    try:
        mm, dd = (int(x) for x in (s or "").split("-"))
        date(2024, mm, dd)          # leap year: accepts 02-29
        return mm, dd
    except Exception:
        raise HTTPException(422, "Dates must look like 04-01 (month-day)")


def _on(year: int, md: tuple[int, int]) -> date:
    mm, dd = md
    return date(year, mm, min(dd, calendar.monthrange(year, mm)[1]))


def season_around(plan: MembershipPlan, today: date) -> tuple[date, date]:
    """The season that contains today, else the next one to start. End is exclusive."""
    s, e = _md(plan.season_start), _md(plan.season_end)
    for y in (today.year - 1, today.year, today.year + 1):
        start = _on(y, s)
        end = _on(y, e)
        if end <= start:
            end = _on(y + 1, e)
        end = end + timedelta(days=1)           # season_end is the last day of the season
        if start <= today < end or today < start:
            return start, end
    raise HTTPException(422, "This plan's season dates don't work")


def window_around(item: MembershipPlanItem, term_start: date) -> Optional[tuple[date, date]]:
    """A seasonal item's window inside a plan year (first occurrence on or after term_start)."""
    if not (item.window_start and item.window_end):
        return None
    s, e = _md(item.window_start), _md(item.window_end)
    for y in (term_start.year, term_start.year + 1):
        start = _on(y, s)
        end = _on(y, e)
        if end < start:
            end = _on(y + 1, e)
        if end >= term_start:
            return start, end + timedelta(days=1)
    return None


# ── plans ────────────────────────────────────────────────────────────────────────────────────────

def plan_offers(plan: MembershipPlan, billing: str) -> Optional[float]:
    return plan.price_monthly if billing == "monthly" else plan.price_yearly


def validate_plan(plan: MembershipPlan) -> None:
    if not (plan.name or "").strip():
        raise HTTPException(422, "Give the plan a name")
    if not plan.price_monthly and not plan.price_yearly:
        raise HTTPException(422, "Set a monthly price, a yearly price, or both")
    for p in (plan.price_monthly, plan.price_yearly):
        if p is not None and p < 0:
            raise HTTPException(422, "Prices can't be negative")
    if plan.term not in ("rolling", "season"):
        raise HTTPException(422, "Plan term must be rolling or season")
    if plan.term == "season":
        _md(plan.season_start)
        _md(plan.season_end)
    if plan.late_join not in ("prorated", "full"):
        raise HTTPException(422, "Late joiners pay prorated or full")


def validate_item(item: MembershipPlanItem) -> None:
    if item.kind not in ITEM_KINDS:
        raise HTTPException(422, "Included items are a service, a discount or a perk")
    if item.kind == "service" and not item.service_id:
        raise HTTPException(422, "Pick the service this plan includes")
    if item.kind == "service" and (item.qty or 0) < 1:
        raise HTTPException(422, "Include the service at least once")
    if item.kind == "discount" and not (0 < (item.pct or 0) <= 100):
        raise HTTPException(422, "A plan discount is between 1 and 100%")
    if item.kind == "discount" and item.applies_to not in ("labour", "parts", "both"):
        raise HTTPException(422, "A plan discount applies to labour, parts or both")
    if item.kind == "perk" and not (item.label or "").strip():
        raise HTTPException(422, "Describe the perk")
    if bool(item.window_start) != bool(item.window_end):
        raise HTTPException(422, "Give the booking window a start and an end")
    if item.window_start:
        _md(item.window_start)
        _md(item.window_end)


async def plan_items(db: AsyncSession, plan_id: str) -> list[MembershipPlanItem]:
    """The plan's current items (items replaced after use are kept with sort_order < 0)."""
    return list((await db.execute(select(MembershipPlanItem).where(
        MembershipPlanItem.plan_id == plan_id, MembershipPlanItem.sort_order >= 0)
        .order_by(MembershipPlanItem.sort_order))).scalars().all())


def eligible(plan: MembershipPlan, device: Optional[Device]) -> bool:
    if device is None:
        return True
    if plan.template_key and device.template_key and plan.template_key != device.template_key:
        return False
    groups = plan.asset_group_ids or []
    return not groups or not device.asset_group_id or device.asset_group_id in groups


# Starter plans per industry template (§6.1). Services are matched to the shop's catalog by name;
# anything not found becomes a perk so the shop can link it after.
STARTERS: dict[str, list[dict]] = {
    "marine": [{"name": "Boat Care", "price_monthly": 45, "price_yearly": 449, "items": [
        {"kind": "service", "match": "launch", "label": "Spring launch", "qty": 1, "window": ("03-01", "05-31")},
        {"kind": "service", "match": "winteri", "label": "Winterisation", "qty": 1, "window": ("10-01", "11-30")},
        {"kind": "service", "match": "haul", "label": "Haul-out", "qty": 1},
        {"kind": "discount", "pct": 10, "applies_to": "labour", "label": "10% off labour"},
        {"kind": "perk", "label": "Priority slot when the yard is busy"}]}],
    "auto": [{"name": "Auto Care", "price_monthly": 19, "price_yearly": 199, "items": [
        {"kind": "service", "match": "oil", "label": "Oil change", "qty": 3},
        {"kind": "service", "match": "rotat", "label": "Tyre rotation", "qty": 2},
        {"kind": "service", "match": "inspect", "label": "Inspection", "qty": 1},
        {"kind": "discount", "pct": 10, "applies_to": "labour", "label": "10% off labour"}]}],
    "electronics": [{"name": "Device Protect", "price_monthly": 9, "price_yearly": 89, "items": [
        {"kind": "service", "match": "screen", "label": "Screen repair", "qty": 1},
        {"kind": "service", "match": "battery", "label": "Battery health check", "qty": 2},
        {"kind": "discount", "pct": 15, "applies_to": "labour", "label": "15% off labour"},
        {"kind": "perk", "label": "Loaner device while yours is in"}]}],
}
STARTERS["tech"] = STARTERS["electronics"]


async def starter(db: AsyncSession, org_id: str, key: str) -> list[dict]:
    """Starter plans for a template, with service ids matched against this shop's catalog."""
    services = (await db.execute(select(Service.id, Service.name).where(Service.org_id == org_id))).all()
    out = []
    for p in STARTERS.get(key, []):
        items = []
        for it in p["items"]:
            row = {"kind": it["kind"], "label": it.get("label", ""), "qty": it.get("qty", 1),
                   "pct": it.get("pct", 0), "applies_to": it.get("applies_to", "labour"),
                   "window_start": (it.get("window") or (None, None))[0],
                   "window_end": (it.get("window") or (None, None))[1], "service_id": None}
            if it["kind"] == "service":
                hit = next((s for s in services if it["match"] in (s.name or "").lower()), None)
                if hit:
                    row["service_id"] = hit.id
                    row["label"] = hit.name
                else:
                    row["kind"] = "perk"           # link it to a service later
            items.append(row)
        out.append({**{k: v for k, v in p.items() if k != "items"}, "template_key": key, "items": items})
    return out


# ── selling ──────────────────────────────────────────────────────────────────────────────────────

def first_term(plan: MembershipPlan, today: date) -> tuple[date, date, str, float]:
    """(term_start, term_end, status, price_factor) for someone joining today."""
    if plan.term != "season":
        return today, add_months(today, 12), "active", 1.0
    start, end = season_around(plan, today)
    if today < start:
        if (start - today).days > (plan.presale_days or 0):
            raise HTTPException(409, f"{plan.name} goes on sale {(plan.presale_days or 0)} days before "
                                     f"the season starts ({start:%d %b %Y}).")
        return start, end, "scheduled", 1.0
    factor = 1.0
    if plan.late_join == "prorated":
        factor = max(0.0, min(1.0, (end - today).days / max(1, (end - start).days)))
    return today, end, "active", factor


def first_period(billing: str, term_start: date, term_end: date) -> tuple[date, date]:
    if billing == "yearly":
        return term_start, term_end
    return term_start, min(add_months(term_start, 1), term_end)


async def live_on_device(db: AsyncSession, org_id: str, device_id: str) -> list[Membership]:
    ids = (await db.execute(select(MembershipAsset.membership_id).where(
        MembershipAsset.org_id == org_id, MembershipAsset.device_id == device_id))).scalars().all()
    if not ids:
        return []
    return list((await db.execute(select(Membership).where(
        Membership.id.in_(ids), Membership.status.in_(LIVE)))).scalars().all())


async def _new_invoice(db: AsyncSession, org_id: str, client_id: str, plan: MembershipPlan,
                       description: str, amount: float, due: date) -> Invoice:
    from backend.services.numbering import next_number
    inv = Invoice(invoice_number=await next_number(db, org_id, "invoice"), org_id=org_id,
                  client_id=client_id, status="draft", subtotal=amount, discount=0.0, tax=0.0,
                  total=amount, amount_paid=0.0, issued_at=datetime.utcnow(),
                  due_at=datetime.combine(due, datetime.min.time()),
                  comment=f"{plan.name} membership")
    db.add(inv)
    await db.flush()
    db.add(InvoiceLine(invoice_id=inv.id, order_line_id=None, kind="membership", source_id=plan.id,
                       sort_order=0, description=description[:500], qty=1, unit_price=amount, total=amount))
    return inv


def _line_text(plan: MembershipPlan, billing: str, device: Optional[Device], start: date, end: date) -> str:
    asset = " ".join(x for x in [device.brand, device.model] if x) if device else ""
    last = end - timedelta(days=1)
    span = f"{start:%d %b %Y} – {last:%d %b %Y}"
    return f"{plan.name} ({billing}){' · ' + asset if asset else ''} · {span}"


async def sell(db: AsyncSession, *, org_id: str, actor: Employee, plan_id: str, client_id: str,
               device_id: Optional[str], billing: str, order_id: Optional[str] = None,
               manager_id: Optional[str] = None, today: Optional[date] = None) -> Membership:
    today = today or date.today()
    if billing not in BILLING:
        raise HTTPException(422, "Choose monthly or yearly")
    plan = (await db.execute(select(MembershipPlan).where(
        MembershipPlan.id == plan_id, MembershipPlan.org_id == org_id))).scalar_one_or_none()
    if not plan or not plan.active:
        raise HTTPException(404, "Plan not found")
    price = plan_offers(plan, billing)
    if not price:
        raise HTTPException(422, f"{plan.name} isn't sold {billing}")
    client = (await db.execute(select(Client).where(
        Client.id == client_id, Client.org_id == org_id))).scalar_one_or_none()
    if not client:
        raise HTTPException(404, "Client not found")
    device = None
    if device_id:
        device = (await db.execute(select(Device).where(
            Device.id == device_id, Device.org_id == org_id))).scalar_one_or_none()
        if not device:
            raise HTTPException(404, "Asset not found")
        if device.client_id and device.client_id != client_id:
            raise HTTPException(409, "That asset belongs to another client")
        if not eligible(plan, device):
            raise HTTPException(409, f"{plan.name} isn't for this kind of asset")
        if any(m.plan_id == plan.id for m in await live_on_device(db, org_id, device.id)):
            raise HTTPException(409, f"This asset already has {plan.name}")
    order = None
    if order_id:
        order = (await db.execute(select(Order).where(
            Order.id == order_id, Order.org_id == org_id))).scalar_one_or_none()
        if not order:
            raise HTTPException(404, "Work order not found")
    if manager_id:
        ok = (await db.execute(select(Employee.id).where(
            Employee.id == manager_id, Employee.org_id == org_id))).scalar_one_or_none()
        if not ok:
            raise HTTPException(404, "Manager not found")

    term_start, term_end, status, factor = first_term(plan, today)
    p_start, p_end = first_period(billing, term_start, term_end)
    amount = q2(price * (factor if billing == "yearly" else 1.0))
    m = Membership(org_id=org_id, plan_id=plan.id, client_id=client.id,
                   device_id=device.id if device else None, billing=billing, price=price,
                   status=status, term_start=term_start, term_end=term_end,
                   period_start=p_start, period_end=p_end,
                   manager_id=manager_id or getattr(client, "manager_id", None) or actor.id,
                   sold_by_id=actor.id)
    db.add(m)
    await db.flush()
    if device:
        db.add(MembershipAsset(org_id=org_id, membership_id=m.id, device_id=device.id))
    period = MembershipPeriod(org_id=org_id, membership_id=m.id, period_start=p_start,
                              period_end=p_end, amount=amount, due_on=today)
    text = _line_text(plan, billing, device, p_start, p_end)
    if order:
        db.add(OrderLine(order_id=order.id, line_type="fee", kind="membership", ref_id=plan.id,
                         description=text[:500], qty=1, unit_price=amount, membership_id=m.id))
        period.order_id = order.id
    else:
        period.invoice_id = (await _new_invoice(db, org_id, client.id, plan, text, amount, today)).id
    db.add(period)
    await db.flush()
    await sync_tags(db, org_id, client.id)
    if order and order.device_id and device and order.device_id == device.id and status == "active":
        await apply_to_order(db, order)
    return m


# ── coverage on work orders ──────────────────────────────────────────────────────────────────────

async def used_in_term(db: AsyncSession, m: Membership, item_id: str) -> float:
    return float((await db.execute(select(func.coalesce(func.sum(MembershipUsage.qty), 0)).where(
        MembershipUsage.membership_id == m.id, MembershipUsage.plan_item_id == item_id,
        MembershipUsage.term_start == m.term_start))).scalar_one() or 0)


async def _pending_lines(db: AsyncSession, m: Membership, service_id: str,
                         exclude_line: Optional[str] = None) -> float:
    """Covered lines on orders that haven't closed yet (no usage row): they hold a count too."""
    q = select(func.coalesce(func.sum(OrderLine.qty), 0)).where(
        OrderLine.membership_id == m.id, OrderLine.ref_id == service_id, OrderLine.kind == "service",
        OrderLine.discount >= 100,
        ~select(MembershipUsage.id).where(MembershipUsage.order_line_id == OrderLine.id).exists())
    if exclude_line:
        q = q.where(OrderLine.id != exclude_line)
    return float((await db.execute(q)).scalar_one() or 0)


async def allowance(db: AsyncSession, m: Membership, item: MembershipPlanItem) -> float:
    """How many of a service item are included this plan year (with roll-over if switched on)."""
    total = float(item.qty or 0)
    if item.rollover:
        prev_start = add_months(m.term_start, -12)
        if m.created_at and m.created_at.date() <= prev_start:
            used_prev = float((await db.execute(select(func.coalesce(func.sum(MembershipUsage.qty), 0)).where(
                MembershipUsage.membership_id == m.id, MembershipUsage.plan_item_id == item.id,
                MembershipUsage.term_start == prev_start))).scalar_one() or 0)
            total += max(0.0, float(item.qty or 0) - used_prev)
    return total


async def remaining(db: AsyncSession, m: Membership, item: MembershipPlanItem,
                    exclude_line: Optional[str] = None) -> float:
    return max(0.0, await allowance(db, m, item) - await used_in_term(db, m, item.id)
               - await _pending_lines(db, m, item.service_id or "", exclude_line))


def _discount_fits(item: MembershipPlanItem, line: OrderLine) -> bool:
    t = (line.line_type or "").lower()
    if item.applies_to == "both":
        return t in ("labour", "part")
    return t == ("labour" if item.applies_to == "labour" else "part")


async def coverage_for_line(db: AsyncSession, memberships: list[Membership],
                            line: OrderLine) -> Optional[tuple[Membership, MembershipPlanItem]]:
    """The plan item that would cover (or discount) this line, if any."""
    if line.kind == "membership" or line.membership_id:
        return None
    best = None
    for m in memberships:
        if m.status != "active":
            continue
        for it in await plan_items(db, m.plan_id):
            if it.kind == "service" and line.kind == "service" and line.ref_id == it.service_id:
                if await remaining(db, m, it) >= (line.qty or 1):
                    return m, it
            elif it.kind == "discount" and _discount_fits(it, line):
                current = (line.discount or 0) if (line.discount_type or "pct") == "pct" else 0
                if (it.pct or 0) > current and (best is None or it.pct > best[1].pct):
                    best = (m, it)
    return best


def _cover(line: OrderLine, m: Membership, item: MembershipPlanItem) -> None:
    line.discount = 100.0 if item.kind == "service" else float(item.pct or 0)
    line.discount_type = "pct"
    # Company-paid keeps the technician's commission whole: the plan, not the tech, gives it away.
    line.discount_paid_by = "company"
    line.membership_id = m.id


def uncover(line: OrderLine) -> None:
    line.discount = 0.0
    line.discount_type = "pct"
    line.discount_paid_by = "client"
    line.membership_id = None


async def apply_to_order(db: AsyncSession, order: Order) -> int:
    """Cover every eligible line on an order. Returns how many lines changed."""
    if not order.device_id:
        return 0
    ms = await live_on_device(db, order.org_id, order.device_id)
    if not ms:
        return 0
    lines = (await db.execute(select(OrderLine).where(OrderLine.order_id == order.id))).scalars().all()
    n = 0
    for line in lines:
        hit = await coverage_for_line(db, ms, line)
        if hit:
            _cover(line, *hit)
            await db.flush()
            n += 1
    return n


async def check_order_lines(db: AsyncSession, order: Order) -> list[OrderLine]:
    """Server-side guard (§7.4): every line carrying a plan must still be allowed by that plan.
    Lines that aren't go back to full price (the caller recomputes their totals)."""
    lines = (await db.execute(select(OrderLine).where(
        OrderLine.order_id == order.id, OrderLine.membership_id.isnot(None),
        OrderLine.kind != "membership"))).scalars().all()
    dropped = []
    for line in lines:
        if (await db.execute(select(MembershipUsage.id).where(
                MembershipUsage.order_line_id == line.id))).scalar_one_or_none():
            continue                                    # already counted at an earlier close
        m = (await db.execute(select(Membership).where(
            Membership.id == line.membership_id, Membership.org_id == order.org_id))).scalar_one_or_none()
        ok = False
        if m and m.status == "active" and order.device_id and order.device_id in (
                await asset_ids(db, m)):
            for it in await plan_items(db, m.plan_id):
                if it.kind == "service" and line.kind == "service" and line.ref_id == it.service_id \
                        and (line.discount or 0) >= 100:
                    ok = await remaining(db, m, it, exclude_line=line.id) >= (line.qty or 1)
                elif it.kind == "discount" and _discount_fits(it, line) and (line.discount or 0) <= (it.pct or 0):
                    ok = True
                if ok:
                    break
        if not ok:
            uncover(line)
            dropped.append(line)
    if dropped:
        await db.flush()
    return dropped


async def asset_ids(db: AsyncSession, m: Membership) -> list[str]:
    return list((await db.execute(select(MembershipAsset.device_id).where(
        MembershipAsset.membership_id == m.id))).scalars().all())


async def record_usage(db: AsyncSession, order: Order) -> int:
    """On close: count every covered line against its plan (once per line)."""
    lines = (await db.execute(select(OrderLine).where(
        OrderLine.order_id == order.id, OrderLine.membership_id.isnot(None),
        OrderLine.kind != "membership"))).scalars().all()
    n = 0
    for line in lines:
        if (await db.execute(select(MembershipUsage.id).where(
                MembershipUsage.order_line_id == line.id))).scalar_one_or_none():
            continue
        m = (await db.execute(select(Membership).where(Membership.id == line.membership_id))).scalar_one_or_none()
        if not m:
            continue
        item = None
        for it in await plan_items(db, m.plan_id):
            if it.kind == "service" and line.ref_id == it.service_id and (line.discount or 0) >= 100:
                item = it
                break
            if it.kind == "discount" and _discount_fits(it, line):
                item = it
        gross = q2((line.qty or 0) * (line.unit_price or 0))
        db.add(MembershipUsage(org_id=order.org_id, membership_id=m.id,
                               plan_item_id=item.id if item else None, device_id=order.device_id,
                               order_id=order.id, order_line_id=line.id,
                               qty=(line.qty or 1) if item and item.kind == "service" else 0,
                               value=q2(gross * (line.discount or 0) / 100), term_start=m.term_start))
        n += 1
    if n:
        await db.flush()
    return n


# ── checkout offer ───────────────────────────────────────────────────────────────────────────────

async def offer_for_order(db: AsyncSession, org: Organisation, order: Order,
                          today: Optional[date] = None) -> Optional[dict]:
    """The plan worth offering a non-member at checkout, or None (§7.3)."""
    today = today or date.today()
    if not org.membership_checkout_prompt or not order.client_id:
        return None
    client = (await db.execute(select(Client).where(Client.id == order.client_id))).scalar_one_or_none()
    if not client or (client.membership_offer_snoozed_until and client.membership_offer_snoozed_until > today):
        return None
    device = None
    if order.device_id:
        device = (await db.execute(select(Device).where(Device.id == order.device_id))).scalar_one_or_none()
        if await live_on_device(db, org.id, order.device_id):
            return None
    plans = (await db.execute(select(MembershipPlan).where(
        MembershipPlan.org_id == org.id, MembershipPlan.active == True))).scalars().all()  # noqa: E712
    lines = (await db.execute(select(OrderLine).where(
        OrderLine.order_id == order.id, OrderLine.kind != "membership"))).scalars().all()
    best = None
    for plan in plans:
        if not eligible(plan, device):
            continue
        if plan.term == "season":
            try:
                first_term(plan, today)
            except HTTPException:
                continue                                # not on sale yet
        items = await plan_items(db, plan.id)
        left = {it.id: float(it.qty or 0) for it in items if it.kind == "service"}
        saving = 0.0
        for line in lines:
            gross = q2((line.qty or 0) * (line.unit_price or 0))
            for it in items:
                if it.kind == "service" and line.kind == "service" and line.ref_id == it.service_id \
                        and left.get(it.id, 0) >= (line.qty or 1):
                    left[it.id] -= line.qty or 1
                    saving += gross
                    break
                if it.kind == "discount" and _discount_fits(it, line):
                    saving += q2(gross * (it.pct or 0) / 100)
                    break
        yearly = plan.price_yearly or q2((plan.price_monthly or 0) * 12)
        if yearly <= 0 or saving <= 0:
            continue
        if saving < yearly * (org.membership_prompt_threshold_pct or 0) / 100:
            continue
        if best is None or saving > best["saving"]:
            best = {"plan_id": plan.id, "name": plan.name, "saving": q2(saving),
                    "price_monthly": plan.price_monthly, "price_yearly": plan.price_yearly,
                    "includes": [label_of(it) for it in items][:4]}
    return best


def label_of(it: MembershipPlanItem, service_name: Optional[str] = None) -> str:
    if it.kind == "service":
        return f"{service_name or it.label or 'Service'} × {it.qty}"
    if it.kind == "discount":
        what = {"labour": "labour", "parts": "parts", "both": "labour and parts"}[it.applies_to or "labour"]
        return f"{int(it.pct) if float(it.pct).is_integer() else it.pct}% off {what}"
    return it.label


# ── tags ─────────────────────────────────────────────────────────────────────────────────────────

async def _system_tag(db: AsyncSession, org_id: str, source: str, name: str, color: str) -> Tag:
    t = (await db.execute(select(Tag).where(Tag.org_id == org_id, Tag.source == source))).scalar_one_or_none()
    if not t:
        t = Tag(org_id=org_id, name=name[:40], color=color, system=True, source=source)
        db.add(t)
        await db.flush()
    elif t.name != name[:40]:
        t.name = name[:40]
    return t


async def sync_tags(db: AsyncSession, org_id: str, client_id: str) -> None:
    """Member / Former member system tags on the client, one pair per plan (§7.9)."""
    rows = (await db.execute(select(Membership.plan_id, Membership.status).where(
        Membership.org_id == org_id, Membership.client_id == client_id))).all()
    by_plan: dict[str, set[str]] = {}
    for pid, st in rows:
        by_plan.setdefault(pid, set()).add(st)
    want: set[str] = set()
    for pid, states in by_plan.items():
        plan = (await db.execute(select(MembershipPlan).where(MembershipPlan.id == pid))).scalar_one_or_none()
        if not plan:
            continue
        if states & set(LIVE):
            want.add((await _system_tag(db, org_id, f"plan:{pid}", f"{plan.name} member", plan.color)).id)
        elif states & set(ENDED):
            want.add((await _system_tag(db, org_id, f"plan-former:{pid}", f"Former {plan.name} member",
                                        "#64748b")).id)
    have = (await db.execute(select(Taggable).join(Tag, Tag.id == Taggable.tag_id).where(
        Taggable.org_id == org_id, Taggable.entity_type == "contact", Taggable.entity_id == client_id,
        Tag.system == True, Tag.source.like("plan%")))).scalars().all()  # noqa: E712
    for row in have:
        if row.tag_id not in want:
            await db.delete(row)
        else:
            want.discard(row.tag_id)
    for tid in want:
        db.add(Taggable(org_id=org_id, tag_id=tid, entity_type="contact", entity_id=client_id))
    await db.flush()


# ── changes ──────────────────────────────────────────────────────────────────────────────────────

async def end(db: AsyncSession, m: Membership, reason: str, today: Optional[date] = None) -> None:
    m.status = "lapsed" if reason == "unpaid" else "cancelled"
    m.ended_on = today or date.today()
    m.ended_reason = reason
    m.renews = False
    await db.flush()
    await sync_tags(db, m.org_id, m.client_id)


def refund_estimate(m: Membership, paid: float, used_value: float, today: date) -> float:
    """Unused share of a paid yearly period, minus what the plan already gave, never below 0."""
    if m.billing != "yearly" or paid <= 0:
        return 0.0
    days = max(1, (m.period_end - m.period_start).days)
    left = max(0, (m.period_end - max(today, m.period_start)).days)
    return max(0.0, q2(paid * left / days - used_value))


async def period_paid(db: AsyncSession, p: MembershipPeriod) -> bool:
    if p.paid_at:
        return True
    if p.invoice_id:
        inv = (await db.execute(select(Invoice).where(Invoice.id == p.invoice_id))).scalar_one_or_none()
        return bool(inv and inv.status == "paid")
    if p.order_id:
        o = (await db.execute(select(Order).where(Order.id == p.order_id))).scalar_one_or_none()
        if not o:
            return False
        from backend.services.order_money import balance_due, net_paid
        return (await net_paid(db, o.id)) > 0 and (await balance_due(db, o)) <= 0
    return False


async def sync_payments(db: AsyncSession, org_id: str, membership_id: Optional[str] = None) -> int:
    """Mark periods paid once their invoice (or work order) is paid; un-pause the member."""
    q = select(MembershipPeriod).where(MembershipPeriod.org_id == org_id, MembershipPeriod.paid_at.is_(None))
    if membership_id:
        q = q.where(MembershipPeriod.membership_id == membership_id)
    n = 0
    for p in (await db.execute(q)).scalars().all():
        if await period_paid(db, p):
            p.paid_at = datetime.utcnow()
            m = (await db.execute(select(Membership).where(Membership.id == p.membership_id))).scalar_one_or_none()
            if m and m.status == "paused" and not await _unpaid_started(db, m, date.today()):
                m.status = "active"
            n += 1
    if n:
        await db.flush()
    return n


async def _unpaid_started(db: AsyncSession, m: Membership, today: date) -> list[MembershipPeriod]:
    return list((await db.execute(select(MembershipPeriod).where(
        MembershipPeriod.membership_id == m.id, MembershipPeriod.paid_at.is_(None),
        MembershipPeriod.due_on <= today))).scalars().all())


def log_key(key: str) -> str:
    """NotificationLog.trigger_event is 60 chars: keep the readable kind, hash the rest."""
    kind, _, rest = key.partition(":")
    return f"{kind}:{hashlib.sha1(rest.encode()).hexdigest()[:24]}"[:60]


# ── messages ─────────────────────────────────────────────────────────────────────────────────────

async def tell_client(db: AsyncSession, org_id: str, client: Client, key: str, subject: str,
                      body: str) -> bool:
    """One message per key per client, by text if we have a number, else email (§9)."""
    from backend.services.notification_rules import _send
    from backend.services.notifications import channel_consented
    seen = (await db.execute(select(NotificationLog.id).where(
        NotificationLog.org_id == org_id, NotificationLog.client_id == client.id,
        NotificationLog.trigger_event == log_key(key)))).scalar_one_or_none()
    if seen:
        return False
    channel = "sms" if client.phone else "email"
    recipient = client.phone if channel == "sms" else client.email
    row = NotificationLog(org_id=org_id, scope="client", channel=channel, trigger_event=log_key(key),
                          client_id=client.id, recipient=recipient, subject=subject, body=body,
                          send_at=datetime.utcnow())
    db.add(row)
    if not recipient:
        row.status, row.error = "skipped", "Client has no phone or email on file"
    elif not channel_consented(client, channel):
        row.status, row.error = "skipped", f"No client consent for {channel}"
    else:
        try:
            res = await _send(db, org_id, channel, recipient=recipient, subject=subject, body=body)
            row.status, row.error = res["status"], res.get("error")
        except Exception as e:  # noqa: BLE001 — a provider hiccup never stops the sweep
            row.status, row.error = "failed", str(e)[:200]
    await db.flush()
    return row.status == "sent"


async def alert_manager(db: AsyncSession, m: Membership, title: str, body: str) -> None:
    from backend.services import push
    if not m.manager_id:
        return
    try:
        await push.notify(db, org_id=m.org_id, employee_id=m.manager_id, kind="membership.alert",
                          title=title, body=body, deep_link="/clients?tab=memberships", commit=False)
    except Exception:  # noqa: BLE001
        log.exception("membership alert failed for %s", m.id)


async def pay_link(db: AsyncSession, p: MembershipPeriod) -> Optional[str]:
    """A payment link for a plan invoice (work-order periods are paid with the order)."""
    if not p.invoice_id or p.paid_at:
        return None
    from backend.models import PaymentIntent
    open_ = (await db.execute(select(PaymentIntent).where(
        PaymentIntent.invoice_id == p.invoice_id, PaymentIntent.status.in_(("created", "pending")))
        .order_by(PaymentIntent.created_at.desc()))).scalars().first()
    if open_ and open_.checkout_url:
        return open_.checkout_url
    inv = (await db.execute(select(Invoice).where(Invoice.id == p.invoice_id))).scalar_one_or_none()
    if not inv:
        return None
    from backend.services.invoicing import balance_due
    from backend.services.payments import create_intent
    try:
        intent = await create_intent(db, org_id=p.org_id, order_id=None, invoice_id=inv.id,
                                     amount=balance_due(inv), account_id=None)
    except Exception as e:  # noqa: BLE001 — no online payments set up: the message says so
        log.info("no pay link for invoice %s: %s", inv.id, e)
        return None
    return intent.checkout_url


# ── the sweep ────────────────────────────────────────────────────────────────────────────────────

async def _shop(db: AsyncSession, org_id: str) -> str:
    return (await db.execute(select(Organisation.name).where(Organisation.id == org_id))).scalar_one_or_none() or "the shop"


async def _next_period(db: AsyncSession, m: Membership, plan: MembershipPlan, today: date) -> MembershipPeriod:
    """Create the period after the current one (a new month, or the renewal), with its invoice."""
    start = m.period_end
    if start >= m.term_end:                              # renewal: a new plan year / season
        if plan.term == "season":
            t_start, t_end = season_around(plan, start)
        else:
            t_start, t_end = start, add_months(start, 12)
        price = plan_offers(plan, m.billing) or m.price  # renewals use today's plan price
    else:
        t_start, t_end, price = m.term_start, m.term_end, m.price
    end_ = t_end if m.billing == "yearly" else min(add_months(max(start, t_start), 1), t_end)
    start = max(start, t_start)
    client = (await db.execute(select(Client).where(Client.id == m.client_id))).scalar_one()
    device = (await db.execute(select(Device).where(Device.id == m.device_id))).scalar_one_or_none() \
        if m.device_id else None
    inv = await _new_invoice(db, m.org_id, client.id, plan, _line_text(plan, m.billing, device, start, end_),
                             q2(price), start)
    p = MembershipPeriod(org_id=m.org_id, membership_id=m.id, period_start=start, period_end=end_,
                         amount=q2(price), invoice_id=inv.id, due_on=start)
    db.add(p)
    await db.flush()
    return p


async def sweep(db: AsyncSession, org: Organisation, today: Optional[date] = None) -> dict:
    """Daily membership work for one shop; safe to run every hour (idempotent per day)."""
    today = today or date.today()
    stats = {"invoiced": 0, "paused": 0, "lapsed": 0, "ended": 0, "messages": 0}
    await sync_payments(db, org.id)
    shop = await _shop(db, org.id)
    ms = (await db.execute(select(Membership).where(
        Membership.org_id == org.id, Membership.status.in_(LIVE)))).scalars().all()
    for m in ms:
        plan = (await db.execute(select(MembershipPlan).where(MembershipPlan.id == m.plan_id))).scalar_one()
        client = (await db.execute(select(Client).where(Client.id == m.client_id))).scalar_one()
        first = (client.name or "there").split()[0]

        if m.status == "scheduled" and m.term_start <= today:
            m.status = "active"

        # Upcoming period: monthly at the period end; renewals `renewal_notice_days` ahead.
        has_next = (await db.execute(select(MembershipPeriod.id).where(
            MembershipPeriod.membership_id == m.id,
            MembershipPeriod.period_start >= m.period_end))).scalars().first()
        renewal = m.period_end >= m.term_end
        lead = timedelta(days=org.membership_renewal_notice_days or 0) if renewal else timedelta(0)
        if m.renews and not has_next and today >= m.period_end - lead:
            p = await _next_period(db, m, plan, today)
            stats["invoiced"] += 1
            link = await pay_link(db, p)
            what = f"Your {plan.name} renews on {p.period_start:%d %b}" if renewal \
                else f"Your {plan.name} payment for {p.period_start:%B} is ready"
            body = f"Hi {first}, {what}: {p.amount:.2f}. " + (f"Pay here: {link}" if link
                                                               else f"Pay at {shop} or reply to this message.")
            stats["messages"] += await tell_client(db, org.id, client, f"membership.due:{p.id}", what, body)

        # Roll into the next period once the current one is over.
        if today >= m.period_end:
            if not m.renews:
                await end(db, m, m.ended_reason or "cancelled", today)
                stats["ended"] += 1
                continue
            nxt = (await db.execute(select(MembershipPeriod).where(
                MembershipPeriod.membership_id == m.id, MembershipPeriod.period_start >= m.period_end)
                .order_by(MembershipPeriod.period_start))).scalars().first()
            if nxt:
                if nxt.period_start >= m.term_end or m.period_end >= m.term_end:
                    t_start, t_end = (season_around(plan, nxt.period_start) if plan.term == "season"
                                      else (nxt.period_start, add_months(nxt.period_start, 12)))
                    m.term_start, m.term_end = t_start, t_end
                    m.price = plan_offers(plan, m.billing) or m.price
                m.period_start, m.period_end = nxt.period_start, nxt.period_end

        # Missed payments (§7.7).
        for p in await _unpaid_started(db, m, today):
            late = (today - p.due_on).days
            if late >= 3 and p.nudges < 1:
                link = await pay_link(db, p)
                stats["messages"] += await tell_client(
                    db, org.id, client, f"membership.reminder:{p.id}", f"{plan.name} payment reminder",
                    f"Hi {first}, your {plan.name} payment of {p.amount:.2f} is still open. "
                    + (f"Pay here: {link}" if link else f"Pay at {shop}."))
                p.nudges = 1
            if late >= (org.membership_grace_days or 0) and m.status == "active":
                m.status = "paused"
                stats["paused"] += 1
                await alert_manager(db, m, f"{client.name}: {plan.name} unpaid",
                                    f"{p.amount:.2f} has been open {late} days. Benefits are paused.")
            if late >= (org.membership_lapse_days or 0) and m.status in ("active", "paused"):
                await end(db, m, "unpaid", today)
                stats["lapsed"] += 1
                await alert_manager(db, m, f"{client.name}: {plan.name} lapsed", "Unpaid for too long.")
                stats["messages"] += await tell_client(
                    db, org.id, client, f"membership.lapsed:{m.id}:{m.term_start}", f"{plan.name} has ended",
                    f"Hi {first}, your {plan.name} has ended because a payment wasn't made. "
                    f"Ask {shop} any time to re-join.")
                break

        if m.status not in ("active", "scheduled"):
            continue
        # Included items: booking windows and "use it before" reminders (§7.6).
        for it in await plan_items(db, m.plan_id):
            if it.kind != "service":
                continue
            left = await remaining(db, m, it)
            if left <= 0:
                continue
            win = window_around(it, m.term_start)
            name = it.label or "service"
            if win and win[0] - timedelta(days=14) <= today < win[1]:
                stats["messages"] += await tell_client(
                    db, org.id, client, f"membership.window:{it.id}:{m.id}:{m.term_start}",
                    f"Book your {name}",
                    f"Hi {first}, your {name} is included in {plan.name}. "
                    f"It's done between {win[0]:%d %b} and {(win[1] - timedelta(days=1)):%d %b}. "
                    f"Contact {shop} to book your slot.")
                if today >= win[1] - timedelta(days=14):
                    key = log_key(f"membership.window-staff:{it.id}:{m.id}:{m.term_start}")
                    if not (await db.execute(select(NotificationLog.id).where(
                            NotificationLog.org_id == org.id, NotificationLog.trigger_event == key))).scalar_one_or_none():
                        db.add(NotificationLog(org_id=org.id, scope="employee", channel="push", trigger_event=key,
                                               client_id=client.id, recipient=m.manager_id or "",
                                               subject="window", body="", status="sent",
                                               send_at=datetime.utcnow()))
                        await alert_manager(db, m, f"{client.name}: {name} not booked",
                                            f"Included in {plan.name}; the window closes "
                                            f"{(win[1] - timedelta(days=1)):%d %b}.")
            elif not win and m.renews and m.term_end - timedelta(days=30) <= today < m.term_end:
                stats["messages"] += await tell_client(
                    db, org.id, client, f"membership.expiring:{it.id}:{m.id}:{m.term_start}",
                    f"{name} still included",
                    f"Hi {first}, you still have {int(left)} {name} left in {plan.name}. "
                    f"Use it before {(m.term_end - timedelta(days=1)):%d %b}.")
    await db.flush()
    return stats


# ── reporting ────────────────────────────────────────────────────────────────────────────────────

async def profitability(db: AsyncSession, org_id: str, start: date, end_: date) -> dict:
    plans = (await db.execute(select(MembershipPlan).where(MembershipPlan.org_id == org_id))).scalars().all()
    out = []
    for plan in plans:
        mids = (await db.execute(select(Membership.id).where(Membership.plan_id == plan.id))).scalars().all()
        live = (await db.execute(select(func.count()).select_from(Membership).where(
            Membership.plan_id == plan.id, Membership.status.in_(LIVE)))).scalar_one()
        money_in = given = 0.0
        if mids:
            money_in = float((await db.execute(select(func.coalesce(func.sum(MembershipPeriod.amount), 0)).where(
                MembershipPeriod.membership_id.in_(mids), MembershipPeriod.paid_at.isnot(None),
                MembershipPeriod.paid_at >= datetime.combine(start, datetime.min.time()),
                MembershipPeriod.paid_at < datetime.combine(end_, datetime.min.time())))).scalar_one() or 0)
            given = float((await db.execute(select(func.coalesce(func.sum(MembershipUsage.value), 0)).where(
                MembershipUsage.membership_id.in_(mids),
                MembershipUsage.used_at >= datetime.combine(start, datetime.min.time()),
                MembershipUsage.used_at < datetime.combine(end_, datetime.min.time())))).scalar_one() or 0)
        out.append({"plan_id": plan.id, "name": plan.name, "members": live, "money_in": q2(money_in),
                    "value_given": q2(given), "margin": q2(money_in - given)})
    return {"plans": out, "prepaid_unearned": await prepaid_unearned(db, org_id)}


async def prepaid_unearned(db: AsyncSession, org_id: str, today: Optional[date] = None) -> float:
    """Paid plan money for days not yet reached (what spreading revenue holds back)."""
    today = today or date.today()
    rows = (await db.execute(select(MembershipPeriod).where(
        MembershipPeriod.org_id == org_id, MembershipPeriod.paid_at.isnot(None),
        MembershipPeriod.period_end > today))).scalars().all()
    total = 0.0
    for p in rows:
        days = max(1, (p.period_end - p.period_start).days)
        left = (p.period_end - max(today, p.period_start)).days
        total += float(p.amount or 0) * left / days
    return q2(total)
