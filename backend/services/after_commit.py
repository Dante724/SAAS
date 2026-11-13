"""Work a session queues for after its commit, savepoint-aware (re-review N4).

SQLAlchemy fires the session's `before_commit` / `after_commit` for a SAVEPOINT release too, and
`after_rollback` for a SAVEPOINT rollback. Hooks that treat those as the real commit or rollback
send things early (before the caller's work is saved) or drop things queued by work that does
commit (every `audit.record` runs in a savepoint). These helpers let a hook tell them apart:

- tag each queued item with `current_tx(session)`, the transaction it was queued in;
- in `before_commit` / `after_commit`, act only when `is_outermost(session)`;
- in `after_soft_rollback(session, rolled_back)`, drop only the items for which
  `undone(tag, rolled_back)` is true: queued inside that transaction or a savepoint within it.
"""
from typing import Any, Optional


def current_tx(session) -> Optional[Any]:
    """The innermost transaction (a savepoint, or the outermost one) the session is in now."""
    sync = getattr(session, "sync_session", session)
    return sync._transaction            # no public accessor for the innermost one in SQLAlchemy 2.0


def is_outermost(session) -> bool:
    """In before_commit / after_commit: is this the real COMMIT (not a SAVEPOINT release)?"""
    tx = current_tx(session)
    return tx is None or tx.parent is None


def undone(tag, rolled_back) -> bool:
    """Was work queued in transaction `tag` undone by rolling back `rolled_back`?"""
    if rolled_back.parent is None:
        return True                     # the outermost rollback undoes everything
    t = tag
    while t is not None:
        if t is rolled_back:
            return True
        t = t.parent
    return False
