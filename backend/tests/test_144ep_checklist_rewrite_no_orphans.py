"""144ep — saving a checklist template must never orphan its sections/items.

Root cause of orphan checklist_sections found in a real DB: `_write_structure` rewrites a template
by deleting its steps and relied on the database's ON DELETE CASCADE to remove their sections. Any
connection without FK enforcement (SQLite before the app enabled the pragma; scripts; a misconfigured
env) left the sections behind, pointing at steps that no longer exist. The rewrite must clean up
explicitly, not depend on the database's cascade.
"""
import os

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.models import ChecklistTemplate
from backend.routers.checklists import SectionIn, StepIn, TemplateIn, _write_structure

STRUCTURE = {"name": "QA", "steps": [
    {"title": "Inspect", "sections": [{"title": "Body", "items": [{"label": "Scratches"}]}]},
    {"title": "Test", "sections": [{"title": "Power", "items": [{"label": "Boots"}]}]},
]}

ORPHAN_SECTIONS = ("SELECT COUNT(*) FROM checklist_sections s WHERE NOT EXISTS "
                   "(SELECT 1 FROM checklist_steps t WHERE t.id = s.step_id)")
ORPHAN_ITEMS = ("SELECT COUNT(*) FROM checklist_template_items i WHERE i.section_id IS NOT NULL AND NOT EXISTS "
                "(SELECT 1 FROM checklist_sections s WHERE s.id = i.section_id)")


async def test_rewrite_leaves_no_orphans_even_without_fk_enforcement(client, tenant):
    r = await client.post("/api/checklist-templates", json=STRUCTURE, headers=tenant)
    assert r.status_code == 201, r.text
    tid = r.json()["id"]
    org_id = (await client.get("/api/auth/me", headers=tenant)).json()["org_id"]

    # A connection WITHOUT foreign-key enforcement — the condition that produced the orphans.
    url = os.environ.get("TEST_DATABASE_URL") or os.environ["DATABASE_URL"]
    eng = create_async_engine(url)
    try:
        async with async_sessionmaker(eng, expire_on_commit=False)() as s:
            if url.startswith("sqlite"):
                await s.execute(text("PRAGMA foreign_keys=OFF"))
            t = await s.get(ChecklistTemplate, tid)
            new = TemplateIn(name="QA v2", steps=[StepIn(title="Only", sections=[SectionIn(title="S")])])
            await _write_structure(s, org_id, t, new)
            await s.commit()
            assert (await s.execute(text(ORPHAN_SECTIONS))).scalar() == 0
            assert (await s.execute(text(ORPHAN_ITEMS))).scalar() == 0
            # and the new structure is really there
            assert (await s.execute(text("SELECT COUNT(*) FROM checklist_steps WHERE template_id = :t"),
                                    {"t": tid})).scalar() == 1
    finally:
        await eng.dispose()
