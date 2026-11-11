"""The migrations must have exactly one head.

Two PRs that each add a migration on the same parent are both green on their own, but once both
merge, `alembic upgrade head` refuses to run ("Multiple head revisions") and every deploy and CI
migrations job fails. This happened when #183 (sds01) and #107 (ot01) both revised idg02. This
test fails on the merge commit that creates a second head, so CI catches it on the PR that lands
second instead of on the next deploy.
"""
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory


def test_migrations_have_a_single_head():
    root = Path(__file__).resolve().parents[2]
    cfg = Config(str(root / "backend" / "alembic.ini"))
    cfg.set_main_option("script_location", str(root / "backend" / "migrations"))
    heads = ScriptDirectory.from_config(cfg).get_heads()
    assert len(heads) == 1, (
        f"alembic has {len(heads)} heads {sorted(heads)}: add a merge revision "
        "(alembic merge -m 'merge heads' <head1> <head2>) or re-chain the newer migration")
