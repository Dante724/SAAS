"""Industry templates — Phase 2 (HTTP API).

Exercises the real endpoints: template get/set, per-template group/brand/model
CRUD, template-aware scan-resolve, CSV import, and the guarantee that a work
order keeps its own template_key after the org switches templates.
"""
import pytest


async def _loc_id(client, H):
    r = await client.get("/api/staff/locations", headers=H)
    data = r.json()
    items = data if isinstance(data, list) else data.get("items", data)
    return items[0]["id"]


async def _client_id(client, H):
    r = await client.post("/api/clients", json={"name": "Test Owner", "phone": "+10000000000"}, headers=H)
    assert r.status_code in (200, 201), r.text
    return r.json()["id"]


# ── template get / set ───────────────────────────────────────────────────────
async def test_get_template_returns_descriptor_and_list(client, tenant):
    r = await client.get("/api/settings/industry-template", headers=tenant)
    assert r.status_code == 200, r.text
    j = r.json()
    assert len(j["templates"]) == 10
    assert j["current"] == "general"            # fresh org (generic) maps to general
    assert j["describe"]["wo_button"] == "+ Order"


async def test_set_template_switches_and_seeds(client, tenant):
    r = await client.put("/api/settings/industry-template", json={"template_key": "auto"}, headers=tenant)
    assert r.status_code == 200, r.text
    assert r.json()["current"] == "auto"
    # now reflected + groups seeded
    j = (await client.get("/api/settings/industry-template", headers=tenant)).json()
    assert j["current"] == "auto"
    assert j["describe"]["wo_button"] == "+ Order"
    g = (await client.get("/api/settings/asset-groups?template_key=auto", headers=tenant)).json()
    assert {x["key"] for x in g["items"]} == {"car", "truck", "suv", "van"}


async def test_unknown_template_rejected(client, tenant):
    r = await client.put("/api/settings/industry-template", json={"template_key": "nope"}, headers=tenant)
    assert r.status_code == 422


async def test_company_endpoint_accepts_new_template_keys(client, tenant):
    # unified registry: the legacy /api/staff/company path accepts the new 9 keys
    r = await client.put("/api/staff/company", json={"industry_template": "mobile_phone"}, headers=tenant)
    assert r.status_code in (200, 204), r.text
    got = (await client.get("/api/staff/company", headers=tenant)).json()
    assert got["industry_template"] == "mobile_phone"
    bad = await client.put("/api/staff/company", json={"industry_template": "nonsense"}, headers=tenant)
    assert bad.status_code == 422


async def test_describe_previews_any_template(client, tenant):
    # org is 'general'; describe should still preview a different template without switching
    r = await client.get("/api/settings/industry-template/describe?template_key=marine", headers=tenant)
    assert r.status_code == 200
    j = r.json()
    assert j["asset_label"] == "Vessel"
    assert j["identifier"]["primary"] == "hin"
    assert {f["key"] for f in j["fields"]} >= {"hin"}
    # org's active template is unchanged by a preview
    cur = (await client.get("/api/settings/industry-template", headers=tenant)).json()["current"]
    assert cur == "general"


# ── group / brand / model CRUD ───────────────────────────────────────────────
async def test_group_crud(client, tenant):
    await client.put("/api/settings/industry-template", json={"template_key": "auto"}, headers=tenant)
    r = await client.post("/api/settings/asset-groups",
                          json={"template_key": "auto", "key": "classic", "label": "Classic"}, headers=tenant)
    assert r.status_code == 201, r.text
    gid = r.json()["id"]
    # duplicate key rejected
    dup = await client.post("/api/settings/asset-groups",
                            json={"template_key": "auto", "key": "classic", "label": "Classic 2"}, headers=tenant)
    assert dup.status_code == 409
    # update
    u = await client.put(f"/api/settings/asset-groups/{gid}", json={"label": "Classic Cars"}, headers=tenant)
    assert u.status_code == 200 and u.json()["label"] == "Classic Cars"
    # soft delete -> disappears from default list
    await client.delete(f"/api/settings/asset-groups/{gid}", headers=tenant)
    live = (await client.get("/api/settings/asset-groups?template_key=auto", headers=tenant)).json()
    assert "classic" not in {x["key"] for x in live["items"]}


async def test_same_brand_name_separate_across_templates_via_api(client, tenant):
    r1 = await client.post("/api/settings/asset-brands",
                           json={"template_key": "mobile_phone", "name": "Yamaha"}, headers=tenant)
    r2 = await client.post("/api/settings/asset-brands",
                           json={"template_key": "auto", "name": "Yamaha"}, headers=tenant)
    assert r1.status_code == 201 and r2.status_code == 201
    assert r1.json()["id"] != r2.json()["id"]
    phone = (await client.get("/api/settings/asset-brands?template_key=mobile_phone", headers=tenant)).json()
    auto = (await client.get("/api/settings/asset-brands?template_key=auto", headers=tenant)).json()
    assert "Yamaha" in {b["name"] for b in phone["items"]}
    assert "Yamaha" in {b["name"] for b in auto["items"]}
    # a phone-only brand never shows under auto
    await client.post("/api/settings/asset-brands",
                      json={"template_key": "mobile_phone", "name": "Nokia"}, headers=tenant)
    auto2 = (await client.get("/api/settings/asset-brands?template_key=auto", headers=tenant)).json()
    assert "Nokia" not in {b["name"] for b in auto2["items"]}


async def test_brand_duplicate_rejected(client, tenant):
    await client.post("/api/settings/asset-brands", json={"template_key": "auto", "name": "Rivian"}, headers=tenant)
    dup = await client.post("/api/settings/asset-brands", json={"template_key": "auto", "name": "rivian"}, headers=tenant)
    assert dup.status_code == 409


async def test_model_chain_and_filter(client, tenant):
    await client.put("/api/settings/industry-template", json={"template_key": "auto"}, headers=tenant)
    b = (await client.post("/api/settings/asset-brands",
                           json={"template_key": "auto", "name": "Nissan"}, headers=tenant)).json()
    m = await client.post("/api/settings/asset-models",
                          json={"template_key": "auto", "brand_id": b["id"], "name": "Leaf"}, headers=tenant)
    assert m.status_code == 201, m.text
    lst = (await client.get(f"/api/settings/asset-models?template_key=auto&brand_id={b['id']}", headers=tenant)).json()
    assert "Leaf" in {x["name"] for x in lst["items"]}
    # model under a brand from another template is rejected
    pb = (await client.post("/api/settings/asset-brands",
                            json={"template_key": "mobile_phone", "name": "PhoneCo"}, headers=tenant)).json()
    bad = await client.post("/api/settings/asset-models",
                            json={"template_key": "auto", "brand_id": pb["id"], "name": "X"}, headers=tenant)
    assert bad.status_code == 404


# ── scan-resolve (template-aware) ────────────────────────────────────────────
async def test_scan_resolve_uses_template_kind(client, tenant):
    phone = (await client.post("/api/assets/scan-resolve",
             json={"identifier": "490154203237518", "template_key": "mobile_phone"}, headers=tenant)).json()
    auto = (await client.post("/api/assets/scan-resolve",
            json={"identifier": "1HGCM82633A004352", "template_key": "auto"}, headers=tenant)).json()
    marine = (await client.post("/api/assets/scan-resolve",
              json={"identifier": "BWCE1234C404", "template_key": "marine"}, headers=tenant)).json()
    assert phone["kind"] == "imei"
    assert auto["kind"] == "vin"
    assert marine["kind"] == "hin"


async def test_scan_resolve_group_override(client, tenant):
    await client.put("/api/settings/industry-template", json={"template_key": "bike"}, headers=tenant)
    groups = (await client.get("/api/settings/asset-groups?template_key=bike", headers=tenant)).json()["items"]
    moto = next(g["id"] for g in groups if g["key"] == "motorcycle")
    bicycle = next(g["id"] for g in groups if g["key"] == "bicycle")
    r_moto = (await client.post("/api/assets/scan-resolve",
              json={"identifier": "1HGCM82633A004352", "template_key": "bike", "asset_group_id": moto}, headers=tenant)).json()
    r_bike = (await client.post("/api/assets/scan-resolve",
              json={"identifier": "SN12345", "template_key": "bike", "asset_group_id": bicycle}, headers=tenant)).json()
    assert r_moto["kind"] == "vin"
    assert r_bike["kind"] == "serial"


async def test_scan_resolve_matches_existing_asset(client, tenant):
    vin = "1HGCM82633A004352"
    # create a company-owned vehicle asset carrying that VIN
    a = await client.post("/api/assets", json={
        "owner_type": "company", "asset_type": "Car", "template_key": "auto",
        "brand": "Honda", "model": "Accord", "vin": vin}, headers=tenant)
    assert a.status_code == 201, a.text
    res = (await client.post("/api/assets/scan-resolve",
           json={"identifier": vin, "template_key": "auto"}, headers=tenant)).json()
    assert res["matched"] is True
    assert res["asset"]["vin"] == vin and res["asset"]["brand"] == "Honda"


# ── order retention across template switch ───────────────────────────────────
async def test_order_keeps_template_after_org_switch(client, tenant):
    await client.put("/api/settings/industry-template", json={"template_key": "auto"}, headers=tenant)
    lid = await _loc_id(client, tenant)
    cid = await _client_id(client, tenant)
    ro = await client.post("/api/orders",
                           json={"client_id": cid, "location_id": lid, "template_key": "auto", "complaint": "brakes"},
                           headers=tenant)
    assert ro.status_code == 201, ro.text
    oid = ro.json()["id"]
    assert ro.json()["template_key"] == "auto"
    # switch org template
    await client.put("/api/settings/industry-template", json={"template_key": "marine"}, headers=tenant)
    got = (await client.get(f"/api/orders/{oid}", headers=tenant)).json()
    assert got["template_key"] == "auto"        # historical record unchanged
    # a new order now uses the new template
    ro2 = await client.post("/api/orders",
                            json={"client_id": cid, "location_id": lid, "complaint": "hull"}, headers=tenant)
    assert ro2.json()["template_key"] == "marine"


# ── CSV import ───────────────────────────────────────────────────────────────
async def test_csv_import_scoped_to_template(client, tenant):
    csv = "brand,model,group\nTesla,Model 3,car\nTesla,Model Y,car\nRivian,R1T,truck"
    r = await client.post("/api/settings/asset-directory/import",
                          json={"template_key": "auto", "csv": csv}, headers=tenant)
    assert r.status_code == 200, r.text
    assert r.json()["brands"] >= 1 and r.json()["models"] >= 2
    brands = (await client.get("/api/settings/asset-brands?template_key=auto", headers=tenant)).json()
    assert "Tesla" in {b["name"] for b in brands["items"]}
    # not leaked to another template
    phone = (await client.get("/api/settings/asset-brands?template_key=mobile_phone", headers=tenant)).json()
    assert "Tesla" not in {b["name"] for b in phone["items"]}
