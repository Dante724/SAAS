"""How a staff member's name appears to customers (booking page, customer messages).

Org setting `public_employee_name`:
- full   — the employee's name as entered (default)
- first  — first name only ("Sam" for "Sam Ortega")
- custom — the employee's own "Name on public pages" (Employee.public_name), else the full name
"""
from typing import Optional

MODES = ("full", "first", "custom")


def public_staff_name(emp, mode: Optional[str]) -> str:
    if emp is None:
        return ""
    full = (getattr(emp, "name", None) or "").strip()
    if mode == "first":
        return full.split()[0] if full else ""
    if mode == "custom":
        return (getattr(emp, "public_name", None) or "").strip() or full
    return full
