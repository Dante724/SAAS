"""Coupon maths shared by the job (engage.apply_coupon) and counter (sales) paths.

QA 2026-10-31 (B): a coupon is a price reduction, not a payment. It is worked out on the
price the customer sees before any added tax, and on a job it is booked as negative line(s)
carrying the same tax rate/mode as the lines it reduces, so the tax goes down with the price
instead of staying on the invoice at the full amount."""
import math

from fastapi import HTTPException

from backend.services.money import q2

KINDS = ("pct", "fixed")


def check_coupon_value(kind: str, value: float) -> None:
    if kind not in KINDS or not (value or 0) > 0:
        raise HTTPException(422, "kind must be pct|fixed with a positive value")
    if kind == "pct" and value > 100:
        raise HTTPException(422, "A percentage coupon can't be more than 100%")
    if not math.isfinite(value) or value >= 1e9:
        raise HTTPException(422, "Coupon value is too large")


def coupon_amount(kind: str, value: float, base: float) -> float:
    """What the coupon takes off `base` (never more than base, never negative)."""
    base = max(float(base or 0), 0.0)
    amt = base * float(value) / 100.0 if kind == "pct" else float(value)
    return q2(min(max(amt, 0.0), base))


def order_coupon_lines(lines, kind: str, value: float, owed: float) -> list[dict]:
    """Split a coupon over a job's lines by tax treatment.

    Returns [{"tax_rate", "tax_mode", "unit_price" (negative), "total" (negative)}], one per
    (rate, mode) group, whose totals add up to what the customer saves (capped at `owed`).
    Lines that are already reductions (gift card, loyalty, earlier coupons) are not discounted."""
    groups: dict = {}
    for ln in lines:
        total = float(ln.total or 0)
        if total <= 0 or float(ln.unit_price or 0) < 0:
            continue
        rate = float(ln.tax_rate or 0)
        mode = "included" if (ln.tax_mode or "imposed") == "included" else "imposed"
        price = total if (mode == "included" or not rate) else total / (1 + rate / 100)
        key = (rate, mode)
        groups[key] = groups.get(key, 0.0) + price
    price_total = sum(groups.values())
    if price_total <= 0:
        return []
    off = coupon_amount(kind, value, price_total)

    def split(amount: float) -> list[dict]:
        out, left = [], q2(amount)
        keys = sorted(groups, key=lambda k: groups[k])          # largest group takes the rounding
        for i, (rate, mode) in enumerate(keys):
            d = left if i == len(keys) - 1 else q2(amount * groups[(rate, mode)] / price_total)
            left = q2(left - d)
            if d <= 0:
                continue
            tot = d if (mode == "included" or not rate) else q2(d * (1 + rate / 100))
            out.append({"tax_rate": rate, "tax_mode": mode, "unit_price": -d, "total": -tot})
        return out

    out = split(off)
    saved = -sum(x["total"] for x in out)
    owed = max(float(owed or 0), 0.0)
    if saved > owed + 1e-9:
        # Partly paid job: shrink the reduction so it never exceeds what's still owed.
        out = split(off * owed / saved)
        over = q2(-sum(x["total"] for x in out) - owed)
        if over > 0 and out:                                  # a rounding cent over: drop it
            x = out[-1]
            x["total"] = q2(x["total"] + over)
            r = x["tax_rate"] / 100 if x["tax_mode"] == "imposed" else 0
            x["unit_price"] = q2(x["total"] / (1 + r))
    return [x for x in out if x["total"] < 0]
