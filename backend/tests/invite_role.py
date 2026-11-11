"""A role to invite a teammate with: invites must name one (a teammate with no role can do nothing)."""


async def any_role(client, headers) -> str:
    r = await client.get("/api/settings/rbac/roles", headers=headers)
    assert r.status_code == 200, r.text
    rows = r.json()
    rows = rows.get("items", rows) if isinstance(rows, dict) else rows
    return rows[0]["id"]
