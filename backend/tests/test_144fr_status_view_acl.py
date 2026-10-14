"""A5 — per-status VIEW ACL: scope_orders excludes a role's hidden statuses."""
from types import SimpleNamespace

from sqlalchemy import select

from backend.models import Order
from backend.services import scoping


def _compiled(q):
    return str(q.compile(compile_kwargs={"literal_binds": True}))


def test_hidden_statuses_added_to_where():
    role = SimpleNamespace(order_scope="all", location_scope="all", location_ids=None,
                           hidden_status_keys=["done", "closed"])
    emp = SimpleNamespace(id="e1")
    q = scoping.scope_orders(select(Order), role, emp)
    sql = _compiled(q)
    assert "status" in sql and "NOT IN" in sql.upper()
    assert "'done'" in sql and "'closed'" in sql


def test_no_hidden_statuses_no_filter():
    role = SimpleNamespace(order_scope="all", location_scope="all", location_ids=None,
                           hidden_status_keys=None)
    emp = SimpleNamespace(id="e1")
    sql = _compiled(scoping.scope_orders(select(Order), role, emp))
    assert "NOT IN" not in sql.upper()


def test_owner_no_role_unfiltered():
    sql = _compiled(scoping.scope_orders(select(Order), None, SimpleNamespace(id="e1")))
    assert "NOT IN" not in sql.upper()
