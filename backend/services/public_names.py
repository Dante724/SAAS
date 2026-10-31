"""WS-C #18 — how a staff member's name appears on public pages (online booking, portal).

The employee's own "Name on documents" (Employee.public_name) always wins. Otherwise the shop's
choice (Organisation.public_employee_name) applies: full | first | first_initial ("Jane D.").
"""
from typing import Optional

MODES = ("full", "first", "first_initial")


def display_name(emp, mode: Optional[str]) -> str:
    own = (getattr(emp, "public_name", None) or "").strip()
    if own:
        return own
    parts = (getattr(emp, "name", None) or "").split()
    if not parts:
        return "Staff"
    if mode == "first":
        return parts[0]
    if mode == "first_initial":
        return parts[0] + (f" {parts[-1][0].upper()}." if len(parts) > 1 else "")
    return " ".join(parts)
