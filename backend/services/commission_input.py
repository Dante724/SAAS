"""Commission rule input checks shared by catalog (per-service) and payroll rules.

QA 2026-10-31 (B): discounts call a money amount "fixed", commissions call it "flat"; both
words now mean flat. Anything else used to be stored (or quietly turned into a percentage)."""
import math

CALC_ALIASES = {"percent": "percent", "pct": "percent", "flat": "flat", "fixed": "flat"}
BASES = ("total", "margin")


def check_commission_calc_type(v: str) -> str:
    out = CALC_ALIASES.get((v or "").strip().lower())
    if not out:
        raise ValueError("calc_type must be percent or flat (fixed)")
    return out


def check_commission_basis(v: str) -> str:
    if v not in BASES:
        raise ValueError("percent_basis must be total or margin")
    return v


def check_commission_amount(calc_type: str | None, v: float) -> float:
    if v is None or not math.isfinite(v) or v < 0:
        raise ValueError("Commission amount can't be negative")
    if calc_type == "percent" and v > 100:
        raise ValueError("A percentage commission can't be more than 100%")
    if v >= 1e9:
        raise ValueError("Commission amount is too large")
    return v
