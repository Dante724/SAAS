"""Payroll & commission engine (Mandate 1).

Event-driven accruals posted to an append-only EmployeeBalanceLedger. All money
math uses Decimal with explicit quantization to 4 dp (no float drift).

Every commission belongs to an accrual group (`accrual_key`: event + line/order + rule +
employee). A group's net is what is still owed for it. Accruing posts only when nothing is
open, under a per-cycle idempotency key, so re-firing an event can't double-dip but a
reopened-and-re-closed order pays again. Claw-backs post the negative of what is still open
(or a refunded share of it), so two reversals of the same order can never take it twice.
"""
from decimal import ROUND_HALF_UP, Decimal
from datetime import datetime
from typing import Optional

from sqlalchemy import case, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import (
    CommissionRule,
    EmployeeBalanceLedger,
    EmployeeCoefficient,
    Order,
    OrderLine,
    Part,
    Refund,
)

Q = Decimal("0.0001")  # 4-dp quantum


def _money(x) -> Decimal:
    """Coerce any numeric to a 4-dp Decimal, half-up rounded."""
    if x is None:
        return Decimal("0")
    return Decimal(str(x)).quantize(Q, rounding=ROUND_HALF_UP)


def _line_net(line) -> Decimal:
    """Price × qty after the line discount, before tax — what the shop actually sells the line
    for. A company-paid discount is the shop's promotion, not the technician's, so it is ignored
    (the same rule the commission basis uses)."""
    gross = _money(line.unit_price) * _money(line.qty)
    if getattr(line, "discount_paid_by", "client") == "company" or not line.discount:
        return _money(gross)
    if (getattr(line, "discount_type", None) or "pct") == "fixed":
        return _money(max(gross - _money(line.discount), Decimal("0")))
    return _money(gross * (Decimal("1") - _money(line.discount) / Decimal("100")))


def _line_splits(line, *, fallback=None):
    """144av #3 — who earns on a line, and their share. Prefer explicit co_technicians
    [{employee_id, share}]; else the single technician (work_share, default 1.0); else the
    fallback (order assignee). Shares are used as-is (callers configure them to sum to 1)."""
    co = getattr(line, "co_technicians", None) or []
    out = [(c.get("employee_id"), float(c.get("share") or 0))
           for c in co if isinstance(c, dict) and c.get("employee_id") and float(c.get("share") or 0) > 0]
    if out:
        return out
    tid = getattr(line, "technician_id", None) or fallback
    if tid:
        return [(tid, float(getattr(line, "work_share", 1.0) or 1.0))]
    return []


class CommissionEngine:
    """Stateless calculator + ledger poster. One instance per request/session."""

    def __init__(self, db: AsyncSession, org_id: str):
        self.db = db
        self.org_id = org_id

    # ── rule lookup ─────────────────────────────────────────────────────────
    async def _rules_for(self, event: str, employee_id: str, category: Optional[str],
                         *, order: Optional[Order] = None, status_key: Optional[str] = None,
                         is_close: bool = True, check_timing: bool = True) -> list[CommissionRule]:
        """Active rules for an event that apply to this employee (specific rules
        win over org-wide ones; a category-specific rule wins over a generic one).
        Also honours Build-70 conditions: work-order type, execution term, and
        calculation timing (on_close | immediately | on_status)."""
        rows = (await self.db.execute(select(CommissionRule).where(
            CommissionRule.org_id == self.org_id,
            CommissionRule.active == True,  # noqa: E712
            CommissionRule.event == event,
            ((CommissionRule.employee_id == employee_id) | (CommissionRule.employee_id.is_(None))),
        ))).scalars().all()
        applicable = [r for r in rows if not r.category or (category and r.category.lower() == category.lower())]
        applicable = [r for r in applicable if self._rule_matches_order(r, order)]
        if check_timing:
            applicable = [r for r in applicable if self._rule_fires_now(r, status_key, is_close)]
        specific = [r for r in applicable if r.employee_id == employee_id]
        return specific or applicable

    async def accrue_for_task(self, task) -> None:
        """Build 86 — commission for closing a task.

        Build 70 already gave rules an execution term (on_time / overdue / any); a task
        records `completed_on_time`, so the same conditions apply unchanged. Idempotent
        per task+rule, so re-completing a reopened task can't pay twice.
        """
        if not task.assigned_to_id:
            return
        rules = (await self.db.execute(select(CommissionRule).where(
            CommissionRule.org_id == self.org_id,
            CommissionRule.active == True,  # noqa: E712
            CommissionRule.event == "task_closed",
            ((CommissionRule.employee_id == task.assigned_to_id)
             | (CommissionRule.employee_id.is_(None)))))).scalars().all()
        for r in rules:
            term = r.term_condition or "any"
            on_time = task.completed_on_time is not False
            if term == "on_time" and not on_time:
                continue
            if term == "overdue" and on_time:
                continue
            amt = self.compute_amount(r, line_total=_money(0), margin=_money(0))
            await self._accrue_once(
                employee_id=task.assigned_to_id, amount=amt,
                event="task_closed", rule_id=r.id, ref_type="task", ref_id=task.id,
                base_key=f"task:{task.id}:{r.id}",
                note=f"Task closed: {task.title}")
        await self.db.commit()

    @staticmethod
    def _rule_matches_order(rule: CommissionRule, order: Optional[Order]) -> bool:
        """Build 70 — work-order-type and execution-term conditions."""
        if order is None:
            return True
        if rule.wo_type_id and rule.wo_type_id != order.type_id:
            return False
        term = rule.term_condition or "any"
        if term == "any":
            return True
        urgent = (order.priority or 0) >= 1
        overdue = bool(order.due_at and order.due_at < datetime.utcnow())
        if term == "urgent":
            return urgent
        if term == "overdue":
            return overdue
        if term == "on_time":
            return not overdue
        return True

    @staticmethod
    def _rule_fires_now(rule: CommissionRule, status_key: Optional[str], is_close: bool) -> bool:
        """Build 70 — calculation timing. `immediately` fires on any status change,
        `on_status` only at the rule's trigger status, default `on_close` at close."""
        timing = rule.calc_timing or "on_close"
        if timing == "immediately":
            return True
        if timing == "on_status":
            return bool(status_key and rule.trigger_status_key == status_key)
        return is_close

    @staticmethod
    def _resolve_exceptional(rules: list[CommissionRule], line) -> list[CommissionRule]:
        """Build 72 — exceptional priority: product → category → profile. Highest
        tier present wins; tiers do not stack (Orderry semantics)."""
        # 144ef — a service line's own exceptional rule wins highest, mirroring
        # the product tier (a line is either a product or a service, never both).
        svc = [r for r in rules if r.rule_level == "service" and getattr(r, "service_id", None)
               and r.service_id == getattr(line, "ref_id", None)]
        if svc:
            return svc
        prod = [r for r in rules if r.rule_level == "product" and r.product_id and r.product_id == getattr(line, "ref_id", None)]
        if prod:
            return prod
        cat = [r for r in rules if r.rule_level == "category" and r.category]
        if cat:
            return cat
        return [r for r in rules if r.rule_level in ("generic", "profile")]

    async def _employee_coeff(self, employee_id: str, *, part_id: Optional[str] = None,
                              category: Optional[str] = None, scope_service: bool = False,
                              context: str = "order") -> Decimal:
        """Build 72 — per-employee coefficient for a product/service or category;
        item-specific wins over category; default 1."""
        rows = (await self.db.execute(select(EmployeeCoefficient).where(
            EmployeeCoefficient.org_id == self.org_id,
            EmployeeCoefficient.employee_id == employee_id,
            EmployeeCoefficient.context == context))).scalars().all()
        if not rows:
            return Decimal("1")
        item_type = "service" if scope_service else "product"
        cat_type = "service_category" if scope_service else "product_category"
        for r in rows:
            if r.scope_type == item_type and part_id and r.scope_ref == part_id:
                return _money(r.coefficient)
        for r in rows:
            if r.scope_type == cat_type and category and r.scope_ref.lower() == category.lower():
                return _money(r.coefficient)
        return Decimal("1")

    # ── math ────────────────────────────────────────────────────────────────
    @staticmethod
    def compute_amount(rule: CommissionRule, *, line_total: Decimal, margin: Decimal) -> Decimal:
        """Return the payout for one rule against one basis.
        percent → (basis * pct/100); flat → fixed amount. Then × coefficient."""
        coeff = _money(rule.coefficient if rule.coefficient is not None else 1)
        if rule.calc_type == "flat":
            base = _money(rule.amount)
        else:
            basis = margin if rule.percent_basis == "margin" else line_total
            base = (_money(basis) * _money(rule.amount) / Decimal("100"))
        return _money(base * coeff)

    # ── ledger posting (idempotent) ──────────────────────────────────────────
    async def _post(self, *, employee_id: str, entry_type: str, amount: Decimal,
                    event: Optional[str], rule_id: Optional[str], ref_type: str,
                    ref_id: str, idempotency_key: str, note: str = "",
                    accrual_key: Optional[str] = None) -> bool:
        """Insert a ledger row unless the idempotency key already exists.
        Returns True if posted, False if it was a duplicate (already accrued)."""
        if amount == 0:
            return False
        existing = (await self.db.execute(select(EmployeeBalanceLedger.id).where(
            EmployeeBalanceLedger.idempotency_key == EmployeeBalanceLedger.normalize_key(idempotency_key)))).scalar_one_or_none()
        if existing:
            return False
        self.db.add(EmployeeBalanceLedger(
            org_id=self.org_id, employee_id=employee_id, entry_type=entry_type,
            amount=amount, event=event, rule_id=rule_id, ref_type=ref_type,
            ref_id=ref_id, idempotency_key=idempotency_key, note=note,
            accrual_key=accrual_key))
        try:
            await self.db.flush()
        except IntegrityError:
            await self.db.rollback()
            return False
        return True

    async def _group_state(self, accrual_key: str) -> tuple[Decimal, int, int]:
        """(net still open, accruals posted, rows) for one accrual group."""
        L = EmployeeBalanceLedger
        net, accruals, rows = (await self.db.execute(select(
            func.coalesce(func.sum(L.amount), 0),
            func.coalesce(func.sum(case((L.entry_type == "commission", 1), else_=0)), 0),
            func.count(L.id),
        ).where(L.org_id == self.org_id, L.accrual_key == accrual_key))).one()
        return _money(net), int(accruals or 0), int(rows or 0)

    async def _accrue_once(self, *, employee_id: str, amount: Decimal, event: str,
                           rule_id: Optional[str], ref_type: str, ref_id: str,
                           base_key: str, note: str) -> bool:
        """Post a commission unless this group already has one open. The first accrual keeps
        the plain key; after a full reversal (reopen, archive) the next close posts under
        `<key>@<n>`, so it pays again instead of colliding with the reversed entry."""
        if amount == 0:
            return False
        key = EmployeeBalanceLedger.normalize_key(base_key)
        net, accruals, _ = await self._group_state(key)
        if net != 0 and (net > 0) == (amount > 0):
            return False
        return await self._post(
            employee_id=employee_id, entry_type="commission", amount=amount, event=event,
            rule_id=rule_id, ref_type=ref_type, ref_id=ref_id,
            idempotency_key=key if accruals == 0 else f"{key}@{accruals}",
            accrual_key=key, note=note)

    async def refunded_fraction(self, order: Order, *, exclude_refund_id: Optional[str] = None) -> Decimal:
        """Share of the order's total that has been refunded (0–1)."""
        q = select(func.coalesce(func.sum(Refund.amount), 0)).where(
            Refund.org_id == self.org_id, Refund.order_id == order.id)
        if exclude_refund_id:
            q = q.where(Refund.id != exclude_refund_id)
        refunded = _money((await self.db.execute(q)).scalar())
        total = _money(order.total)
        if refunded <= 0:
            return Decimal("0")
        if total <= 0:
            return Decimal("1")
        return min(Decimal("1"), refunded / total)

    # ── public events ─────────────────────────────────────────────────────────
    async def accrue_for_order_close(self, order: Order):
        """Order entered a closed state → run every on_close (and immediately, and
        matching on_status) rule. Safe to call repeatedly (idempotency keys)."""
        await self._accrue(order, status_key=order.status, is_close=True)

    async def accrue_on_status(self, order: Order, status_key: str):
        """Order reached a non-closing status → run `immediately` rules and any
        `on_status` rule whose trigger matches."""
        await self._accrue(order, status_key=status_key, is_close=False)

    async def _accrue(self, order: Order, *, status_key: Optional[str], is_close: bool):
        # Money already refunded on this order doesn't earn commission (a re-close after a
        # partial refund pays on what the shop kept).
        keep = Decimal("1") - await self.refunded_fraction(order)
        if keep <= 0:
            await self.db.commit()
            return
        if order.assigned_to_id:
            order_total = _money(order.total)
            # Build 144ds — commission by close outcome. The general `order_closed`
            # event still fires for any close; additionally, `order_closed_success`
            # fires when the terminal status is in the "closed" group and
            # `order_closed_fail` when it's "dropped" (unsuccessful). A shop can pay
            # only for successful outcomes, or different amounts for each.
            outcome_event = None
            if is_close:
                from backend.models import OrderStatus as _OS
                grp = (await self.db.execute(select(_OS.status_group).where(
                    _OS.org_id == order.org_id, _OS.key == order.status,
                    _OS.is_estimate == bool(order.is_estimate)))).scalar_one_or_none()
                if grp == "closed":
                    outcome_event = "order_closed_success"
                elif grp == "dropped":
                    outcome_event = "order_closed_fail"
            for ev in [e for e in ("order_closed", outcome_event) if e]:
                for r in await self._rules_for(ev, order.assigned_to_id, None,
                                               order=order, status_key=status_key, is_close=is_close):
                    amt = _money(self.compute_amount(r, line_total=order_total, margin=order_total) * keep)
                    await self._accrue_once(
                        employee_id=order.assigned_to_id, amount=amt,
                        event=ev, rule_id=r.id, ref_type="order", ref_id=order.id,
                        base_key=f"{ev}:{order.id}:{r.id}",
                        note=f"Closing commission for {order.order_number}")

        for line in (order.lines or []):
            tech_id = line.technician_id or order.assigned_to_id
            if not tech_id:
                continue
            line_total = _money(line.total if line.total else _money(line.unit_price) * _money(line.qty))
            if getattr(line, "discount_paid_by", "client") == "company":
                line_total = _money(_money(line.unit_price) * _money(line.qty))
            if line.kind == "product":
                mac = await self._mac_for_part(line.ref_id)
                # Margin on what the line actually sold for: a discount comes out of margin.
                margin = _money(_line_net(line) - mac * _money(line.qty))
                deducted = not getattr(line, "no_deduction", False)
                event = "product_sold" if deducted else "product_sold_no_deduction"
                category = await self._part_category(line.ref_id)
                is_service = False
            elif line.line_type == "labour":
                margin = line_total
                event = "labor_performed"
                category = None
                is_service = True
            else:
                continue
            # 144av #3 — a line can be split across techs (co_technicians); each gets their
            # share of the line's commission + margin. Falls back to the single technician.
            splits = _line_splits(line, fallback=tech_id)
            for emp_id, share in splits:
                sh = _money(share)
                await self._pay_line(event, emp_id, line, order, _money(line_total) * sh * keep,
                                     _money(margin) * sh * keep, category, is_service,
                                     status_key=status_key, is_close=is_close)
            # Manager/assignee override is paid once on the whole line, not per split.
            assignee = order.assigned_to_id
            if assignee and assignee not in [e for e, _ in splits]:
                a_event = "assignee_labor" if event == "labor_performed" else "assignee_product"
                await self._pay_line(a_event, assignee, line, order, line_total * keep, margin * keep,
                                     category, is_service, status_key=status_key, is_close=is_close)
        await self.db.commit()

    async def _pay_line(self, event, employee_id, line, order, line_total, margin, category, is_service,
                        *, status_key, is_close):
        """Resolve the winning exceptional rule (product→category→profile), apply
        the per-employee coefficient, and post the commission."""
        rules = await self._rules_for(event, employee_id, category,
                                      order=order, status_key=status_key, is_close=is_close)
        rules = self._resolve_exceptional(rules, line)
        if not rules:
            return
        coeff = await self._employee_coeff(
            employee_id, part_id=line.ref_id, category=category, scope_service=is_service)
        for r in rules:
            amt = _money(self.compute_amount(r, line_total=line_total, margin=margin) * coeff)
            await self._accrue_once(
                employee_id=employee_id, amount=amt,
                event=event, rule_id=r.id, ref_type="order_line", ref_id=line.id,
                base_key=f"{event}:{line.id}:{r.id}:{employee_id}",
                note=f"{event} on {order.order_number}")

    async def commissions_for_order_create(self, order: Order, creator_id: str):
        """order_created commissions to the creating employee. Creation is its own
        trigger, so timing gates are bypassed."""
        for r in await self._rules_for("order_created", creator_id, None,
                                       order=order, check_timing=False):
            amt = self.compute_amount(r, line_total=_money(order.total or 0), margin=_money(0))
            await self._accrue_once(
                employee_id=creator_id, amount=amt,
                event="order_created", rule_id=r.id, ref_type="order", ref_id=order.id,
                base_key=f"create:{order.id}:{r.id}",
                note=f"Creation commission for {order.order_number}")
        await self.db.commit()

    async def clawback_for_order_reopen(self, order: Order):
        """Order left a closed state: reverse what is still open of every commission earned by
        working or closing it. Creation commission is not tied to closing, so it stays; the
        next close accrues a fresh cycle."""
        await self._reverse(order, tag="reopen", key_prefix="reopen", skip_events=("order_created",))

    async def clawback_for_refund(self, order: Order, refund_id: str, amount):
        """A refund takes back the refunded share of what is still open on the order. The share
        is of what was not refunded before, so successive partial refunds add up to the whole
        commission when the whole order is refunded."""
        prior = await self.refunded_fraction(order, exclude_refund_id=refund_id)
        total = _money(order.total)
        remaining = total * (Decimal("1") - prior)
        share = Decimal("1") if remaining <= 0 else min(Decimal("1"), _money(amount) / remaining)
        await self._reverse(order, tag="refund", key_prefix=f"refund:{refund_id}", share=share,
                            per_refund=True)

    async def reverse_for_deletion(self, order: Order):
        """Build 70 — order deleted/archived: reverse what is still open of commissions whose
        rule has cancel_if_deleted (default), as a current-period adjustment."""
        await self._reverse(order, tag="deletion", key_prefix="deletion", respect_cancel_flag=True)

    async def _order_refs_async(self, order_id: str) -> list[str]:
        line_ids = (await self.db.execute(select(OrderLine.id).where(
            OrderLine.order_id == order_id))).scalars().all()
        return [order_id, *line_ids]

    async def _reverse(self, order: Order, *, tag: str, key_prefix: str,
                       share: Decimal = Decimal("1"), respect_cancel_flag: bool = False,
                       skip_events: tuple = (), per_refund: bool = False):
        refs = await self._order_refs_async(order.id)
        rows = (await self.db.execute(select(EmployeeBalanceLedger).where(
            EmployeeBalanceLedger.org_id == self.org_id,
            EmployeeBalanceLedger.entry_type.in_(("commission", "clawback")),
            EmployeeBalanceLedger.accrual_key.isnot(None),
            EmployeeBalanceLedger.ref_id.in_(refs))
            .order_by(EmployeeBalanceLedger.created_at))).scalars().all()
        groups: dict[str, list] = {}
        for e in rows:
            groups.setdefault(e.accrual_key, []).append(e)
        for key, entries in groups.items():
            first = next((e for e in entries if e.entry_type == "commission"), None)
            if first is None or first.event in skip_events:
                continue
            if respect_cancel_flag and first.rule_id:
                flag = (await self.db.execute(select(CommissionRule.cancel_if_deleted).where(
                    CommissionRule.id == first.rule_id))).scalar_one_or_none()
                if flag is False:
                    continue
            # The group's net is read fresh, so a claw-back posted by an earlier reversal of
            # this order (refund, then archive) is never taken again.
            net, _, n_rows = await self._group_state(key)
            if net == 0 or (net > 0) != (_money(first.amount) > 0):
                continue
            amt = _money(net * share)
            if amt == 0:
                continue
            await self._post(
                employee_id=first.employee_id, entry_type="clawback", amount=-amt,
                event=first.event, rule_id=first.rule_id, ref_type=tag, ref_id=order.id,
                idempotency_key=f"{key_prefix}:{key}" if per_refund else f"{key_prefix}:{key}@{n_rows}",
                accrual_key=key,
                note=f"{tag.capitalize()} reversal of {first.event} ({order.order_number})")
        await self.db.commit()

    # ── helpers ───────────────────────────────────────────────────────────────
    async def _mac_for_part(self, part_id: Optional[str]) -> Decimal:
        if not part_id:
            return Decimal("0")
        p = (await self.db.execute(select(Part).where(Part.id == part_id))).scalar_one_or_none()
        if not p:
            return Decimal("0")
        return _money(p.avg_cost or p.cost_price or 0)

    async def _part_category(self, part_id: Optional[str]) -> Optional[str]:
        if not part_id:
            return None
        p = (await self.db.execute(select(Part).where(Part.id == part_id))).scalar_one_or_none()
        return getattr(p, "category", None) if p else None

    async def balance(self, employee_id: str) -> Decimal:
        total = (await self.db.execute(select(func.coalesce(func.sum(EmployeeBalanceLedger.amount), 0))
            .where(EmployeeBalanceLedger.org_id == self.org_id,
                   EmployeeBalanceLedger.employee_id == employee_id))).scalar()
        return _money(total)
