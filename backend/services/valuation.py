"""What stock on hand is worth, defined once.

A product's stock is valued at one cost basis: its moving-average cost (`Part.avg_cost`, kept by
services/stock_posting on every receipt), else its list cost (`cost_price`) when no average has been
set yet. The product's Details tab, its Stock tab and the stock-value reports all read it here, so
they can't disagree (design audit BUG-04: Details showed 0 where the Stock tab showed 80,000).
"""
from backend.services.money import q2


def unit_cost(part) -> float:
    """The cost one unit of `part` is valued at (0 when there's no part or no cost at all)."""
    if part is None:
        return 0.0
    return float(part.avg_cost or part.cost_price or 0)


def stock_value(part, qty) -> float:
    """`qty` units of `part` at its valuation cost, to the cent."""
    return q2(float(qty or 0) * unit_cost(part))
