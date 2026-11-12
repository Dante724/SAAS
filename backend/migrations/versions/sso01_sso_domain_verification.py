"""SSO email domain: proof of ownership (audit SEC-07).

Any shop could claim any email domain for SSO routing ("first come wins"), so the sign-in page
could send another company's staff to an identity provider the claiming shop controls. A domain
now routes only once the shop publishes a DNS TXT record carrying its token:

- sso_connections.domain_token        the value to publish (set by the app; old rows get one the
                                      next time Settings shows them)
- sso_connections.domain_verified_at  when the TXT record was found; NULL = routes nothing

Existing connections start unverified: none could complete a sign-in yet (the OIDC callback is
not built), so nothing that works today stops working.

Revision ID: sso01
Revises: srch01
"""
import sqlalchemy as sa
from alembic import op

revision = "sso01"
down_revision = "srch01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("sso_connections", schema=None) as batch_op:
        batch_op.add_column(sa.Column("domain_token", sa.String(length=64), nullable=True))
        batch_op.add_column(sa.Column("domain_verified_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("sso_connections", schema=None) as batch_op:
        batch_op.drop_column("domain_verified_at")
        batch_op.drop_column("domain_token")
