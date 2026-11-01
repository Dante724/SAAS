"""How a staff member's name appears to customers (booking page, portal, customer messages).

Org setting `public_employee_name`:
- full          — the employee's name as entered (default)
- first         — first name only ("Sam" for "Sam Ortega")
- first_initial — first name and last initial ("Sam O.")
- custom        — the employee's own "Name on public pages" (Employee.public_name), else the full name
"""
from typing import Optional

MODES = ("full", "first", "first_initial", "custom")


def public_staff_name(emp, mode: Optional[str]) -> str:
    if emp is None:
        return ""
    parts = (getattr(emp, "name", None) or "").split()
    if mode == "custom":
        own = (getattr(emp, "public_name", None) or "").strip()
        if own:
            return own
    if not parts:
        return "Staff"
    if mode == "first":
        return parts[0]
    if mode == "first_initial":
        return parts[0] + (f" {parts[-1][0].upper()}." if len(parts) > 1 else "")
    return " ".join(parts)
