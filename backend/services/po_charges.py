"""A purchase order's optional charges (shipping, transit, customs): a JSON list of
{"label", "amount"} on the PO. One reading of it for the PO total (routers/inventory) and the
nightly reconcile (services/reconcile), so the two can never disagree about what a PO is worth."""
from backend.services.money import q2


def charge_rows(charges) -> list:
    return [c for c in (charges or []) if isinstance(c, dict)]


def charges_total(charges) -> float:
    return q2(sum(float(c.get("amount") or 0) for c in charge_rows(charges)))
