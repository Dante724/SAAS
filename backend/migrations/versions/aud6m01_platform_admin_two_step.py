"""Platform admin: 2-step sign-in and revocable tokens (audit 29 Oct 2026, M6).

- token_version: bumped to sign the admin out everywhere; tokens carry it as "tv".
- mfa_enabled / mfa_secret (encrypted with services/crypto) / mfa_last_step (replay guard).

Revision ID: aud6m01
Revises: mkw01
"""

revision = "aud6m01"
down_revision = "mkw01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Superseded in the merged launch line by ad144s1 (admin panel sprint 1), which adds the same columns (platform_admins.token_version / mfa_secret / mfa_last_step; 2-step is mfa_enrolled_at there).
    # Kept as a no-op so the revision chain (and any database already stamped here) stays valid.
    pass


def downgrade() -> None:
    pass
