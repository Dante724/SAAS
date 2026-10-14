"""C — per-account (cashbox) ACL: a role restricted to some accounts only sees those."""
from types import SimpleNamespace

from backend.services import scoping


def test_allowed_accounts_none_when_unrestricted():
    assert scoping.allowed_accounts(None) is None
    assert scoping.allowed_accounts(SimpleNamespace(account_ids=None)) is None
    assert scoping.allowed_accounts(SimpleNamespace(account_ids=[])) is None


def test_allowed_accounts_restricted_list():
    role = SimpleNamespace(account_ids=["a1", "a2"])
    assert scoping.allowed_accounts(role) == ["a1", "a2"]


def test_may_use_account_respects_restriction():
    role = SimpleNamespace(account_ids=["a1"])
    assert scoping.may_use_account(role, "a1") is True
    assert scoping.may_use_account(role, "a2") is False
    # unrestricted role or missing id → allowed
    assert scoping.may_use_account(None, "a2") is True
    assert scoping.may_use_account(role, None) is True
