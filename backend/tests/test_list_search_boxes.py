"""The search boxes on the Clients, Suppliers, Assets and Products lists.

- Clients / Suppliers: name (any part, any case, any word order), phone however it's typed
  (spaces, dashes, brackets, +/country code, trunk 0) or email.
- Assets: serial / IMEI / VIN, brand + model in any order, or the owner client's name.
- Products: SKU / code (separators and case ignored), barcode (main or extra) or name.

Another shop's records never match."""
import uuid

import pytest


async def _shop(client, country="US"):
    r = await client.post("/api/auth/register", json={
        "org_name": f"Shop {uuid.uuid4().hex[:6]}", "name": "Admin", "country": country,
        "email": f"admin-{uuid.uuid4().hex[:8]}@test.dev", "password": "pw-test-12345"})
    assert r.status_code == 201, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


async def _post(client, h, url, **kw):
    r = await client.post(url, json=kw, headers=h)
    assert r.status_code in (200, 201), r.text
    return r.json()["id"]


async def _ids(client, h, url, text, param="search"):
    r = await client.get(url, params={param: text}, headers=h)
    assert r.status_code == 200, r.text
    return {i["id"] for i in r.json()["items"]}


# ── Clients ────────────────────────────────────────────────────────────────────

@pytest.fixture
async def clients(client):
    h = await _shop(client)
    jane = await _post(client, h, "/api/clients", name="Jane O'Neill", phone="(415) 555-0134",
                       email="jane.oneill@example.com")
    bob = await _post(client, h, "/api/clients", name="Bob Stone", phone="020 7946 0958",
                      email="bob@stone.co.uk")
    acme = await _post(client, h, "/api/clients", name="Acme Plumbing Ltd",
                       client_type="organization", phone="+1 212 555 0199")
    return h, {"jane": jane, "bob": bob, "acme": acme}


async def _clients(client, h, text, param="search"):
    return await _ids(client, h, "/api/clients", text, param)


@pytest.mark.asyncio
async def test_clients_by_name_partial_any_case_any_order(client, clients):
    h, c = clients
    for typed in ("jane", "JANE", "neill", "Jane O'Neill", "o'neill jane", "  jane   o'n "):
        assert await _clients(client, h, typed) == {c["jane"]}, typed
    assert await _clients(client, h, "plumbing acme") == {c["acme"]}
    assert await _clients(client, h, "jane stone") == set()   # every word, one client


@pytest.mark.asyncio
async def test_clients_by_phone_typed_differently_than_stored(client, clients):
    h, c = clients
    for typed in ("4155550134", "415-555-0134", "415.555.0134", "+1 415 555 0134",
                  "1-415-555-0134", "(415)555", "555 0134"):
        assert await _clients(client, h, typed) == {c["jane"]}, typed
    for typed in ("(212) 555-0199", "2125550199", "001 212 555 0199"):
        assert await _clients(client, h, typed) == {c["acme"]}, typed
    for typed in ("+44 20 7946 0958", "0044 20 7946 0958", "02079460958", "7946-0958"):
        assert await _clients(client, h, typed) == {c["bob"]}, typed


@pytest.mark.asyncio
async def test_clients_by_email_and_q_alias_and_total(client, clients):
    h, c = clients
    assert await _clients(client, h, "jane.oneill@example.com") == {c["jane"]}
    assert await _clients(client, h, "STONE.CO") == {c["bob"]}
    assert await _clients(client, h, "+1 415 555 0134", param="q") == {c["jane"]}
    r = (await client.get("/api/clients", params={"search": "415 555 0134"}, headers=h)).json()
    assert r["total"] == 1


@pytest.mark.asyncio
async def test_clients_another_shop_never_matches(client, clients):
    h, c = clients
    other = await _shop(client)
    twin = await _post(client, other, "/api/clients", name="Jane O'Neill",
                       phone="(415) 555-0134", email="jane.oneill@example.com")
    for typed in ("jane", "4155550134", "+1 415 555 0134", "jane.oneill@example.com"):
        assert await _clients(client, h, typed) == {c["jane"]}, typed
        assert await _clients(client, other, typed) == {twin}, typed
    assert await _clients(client, other, "bob") == set()


# ── Suppliers ──────────────────────────────────────────────────────────────────

@pytest.fixture
async def suppliers(client):
    h = await _shop(client)
    parts = await _post(client, h, "/api/suppliers", name="Mobile Parts Direct",
                        contact_name="Sam Lee", phone="+1 (646) 555-0101",
                        email="orders@mpd.example")
    glass = await _post(client, h, "/api/suppliers", name="Glass & Screens Co",
                        phone="0161 496 0000", email="sales@glass.example")
    return h, {"parts": parts, "glass": glass}


async def _suppliers(client, h, text):
    return await _ids(client, h, "/api/suppliers", text)


@pytest.mark.asyncio
async def test_suppliers_by_name_partial_any_case_any_order(client, suppliers):
    h, s = suppliers
    for typed in ("mobile", "PARTS", "direct parts", "Mobile Parts Direct"):
        assert await _suppliers(client, h, typed) == {s["parts"]}, typed
    assert await _suppliers(client, h, "screens glass") == {s["glass"]}
    assert await _suppliers(client, h, "lee sam") == {s["parts"]}      # the contact person
    assert await _suppliers(client, h, "mobile glass") == set()


@pytest.mark.asyncio
async def test_suppliers_by_phone_typed_differently_than_stored(client, suppliers):
    h, s = suppliers
    for typed in ("6465550101", "646-555-0101", "(646) 555 0101", "1 646 555 0101", "555-0101"):
        assert await _suppliers(client, h, typed) == {s["parts"]}, typed
    for typed in ("+44 161 496 0000", "01614960000", "161-496-0000"):
        assert await _suppliers(client, h, typed) == {s["glass"]}, typed


@pytest.mark.asyncio
async def test_suppliers_by_email(client, suppliers):
    h, s = suppliers
    assert await _suppliers(client, h, "orders@mpd.example") == {s["parts"]}
    assert await _suppliers(client, h, "GLASS.EXAMPLE") == {s["glass"]}


@pytest.mark.asyncio
async def test_suppliers_another_shop_never_matches(client, suppliers):
    h, s = suppliers
    other = await _shop(client)
    twin = await _post(client, other, "/api/suppliers", name="Mobile Parts Direct",
                       phone="+1 (646) 555-0101", email="orders@mpd.example")
    for typed in ("mobile parts", "6465550101", "orders@mpd.example"):
        assert await _suppliers(client, h, typed) == {s["parts"]}, typed
        assert await _suppliers(client, other, typed) == {twin}, typed
    assert await _suppliers(client, other, "glass") == set()


# ── Assets ─────────────────────────────────────────────────────────────────────

@pytest.fixture
async def assets(client):
    h = await _shop(client)
    jane = await _post(client, h, "/api/clients", name="Jane O'Neill")
    john = await _post(client, h, "/api/clients", name="John Smith")
    phone = await _post(client, h, "/api/assets", client_id=jane, brand="Apple",
                        model="iPhone 12 Pro", serial="F2LXK0ABCD12", imei="356938035643809")
    car = await _post(client, h, "/api/assets", client_id=john, asset_type="Vehicle",
                      brand="Ford", model="Transit", serial="CAR-0001",
                      vin="1FTBW2CM5HKA12345")
    boat = await _post(client, h, "/api/assets", client_id=john, asset_type="Boat",
                       brand="Boston Whaler", model="Montauk", hin="BWC12345H506")
    return h, {"phone": phone, "car": car, "boat": boat}


async def _assets(client, h, text):
    return await _ids(client, h, "/api/assets", text)


@pytest.mark.asyncio
async def test_assets_by_serial_imei_vin(client, assets):
    h, a = assets
    for typed in ("F2LXK0ABCD12", "f2lxk0abcd12", "ABCD12", "F2LX-K0AB-CD12"):
        assert await _assets(client, h, typed) == {a["phone"]}, typed
    for typed in ("356938035643809", "35693803", "35-693803-564380-9"):
        assert await _assets(client, h, typed) == {a["phone"]}, typed
    for typed in ("1FTBW2CM5HKA12345", "hka12345", "car0001"):
        assert await _assets(client, h, typed) == {a["car"]}, typed
    for typed in ("BWC12345H506", "bwc12345h506", "BWC-12345-H506", "12345h506"):
        assert await _assets(client, h, typed) == {a["boat"]}, typed


@pytest.mark.asyncio
async def test_assets_by_brand_and_model_any_order(client, assets):
    h, a = assets
    for typed in ("apple", "iphone", "Apple iPhone", "iphone 12 apple", "IPHONE 12 PRO"):
        assert await _assets(client, h, typed) == {a["phone"]}, typed
    assert await _assets(client, h, "ford transit") == {a["car"]}
    assert await _assets(client, h, "apple transit") == set()


@pytest.mark.asyncio
async def test_assets_by_owner_name(client, assets):
    h, a = assets
    for typed in ("jane", "o'neill", "O'Neill Jane"):
        assert await _assets(client, h, typed) == {a["phone"]}, typed
    for typed in ("smith", "smith john", "john smith"):
        assert await _assets(client, h, typed) == {a["car"], a["boat"]}, typed
    assert await _assets(client, h, "john iphone") == set()


@pytest.mark.asyncio
async def test_assets_another_shop_never_matches(client, assets):
    h, a = assets
    other = await _shop(client)
    owner = await _post(client, other, "/api/clients", name="Jane O'Neill")
    twin = await _post(client, other, "/api/assets", client_id=owner, brand="Apple",
                       model="iPhone 12 Pro", serial="F2LXK0ABCD12", imei="356938035643809")
    for typed in ("F2LXK0ABCD12", "356938035643809", "apple iphone", "jane"):
        assert await _assets(client, h, typed) == {a["phone"]}, typed
        assert await _assets(client, other, typed) == {twin}, typed
    assert await _assets(client, other, "smith") == set()


# ── Products ───────────────────────────────────────────────────────────────────

@pytest.fixture
async def products(client):
    h = await _shop(client)
    screen = await _post(client, h, "/api/catalog/products", name="iPhone 12 Screen OLED",
                         sku="SCR-IP12-OLED", code="P-1001", barcode="0012345678905")
    battery = await _post(client, h, "/api/catalog/products", name="Galaxy S21 Battery",
                          sku="BAT-S21", code="P-1002")
    r = await client.post(f"/api/catalog/products/{battery}/barcodes",
                          json={"barcode": "4006381333931"}, headers=h)
    assert r.status_code == 201, r.text
    return h, {"screen": screen, "battery": battery}


async def _products(client, h, text):
    mine = await _ids(client, h, "/api/catalog/products", text)
    parts = await _ids(client, h, "/api/inventory/parts", text)     # POS / pickers
    assert mine == parts, text
    return mine


@pytest.mark.asyncio
async def test_products_by_sku_or_code(client, products):
    h, p = products
    for typed in ("SCR-IP12-OLED", "scr-ip12-oled", "SCRIP12OLED", "scr ip12", "ip12-oled"):
        assert await _products(client, h, typed) == {p["screen"]}, typed
    for typed in ("P-1002", "p1002", "BAT-S21", "bats21"):
        assert await _products(client, h, typed) == {p["battery"]}, typed


@pytest.mark.asyncio
async def test_products_by_barcode(client, products):
    h, p = products
    for typed in ("0012345678905", "012345678905"):       # EAN-13 and its UPC-A form
        assert await _products(client, h, typed) == {p["screen"]}, typed
    assert await _products(client, h, "4006381333931") == {p["battery"]}   # extra barcode


@pytest.mark.asyncio
async def test_products_by_name_any_case_any_order(client, products):
    h, p = products
    for typed in ("screen", "IPHONE", "screen iphone", "oled 12 iphone"):
        assert await _products(client, h, typed) == {p["screen"]}, typed
    assert await _products(client, h, "battery galaxy") == {p["battery"]}
    assert await _products(client, h, "galaxy screen") == set()


@pytest.mark.asyncio
async def test_products_another_shop_never_matches(client, products):
    h, p = products
    other = await _shop(client)
    twin = await _post(client, other, "/api/catalog/products", name="iPhone 12 Screen OLED",
                       sku="SCR-IP12-OLED", barcode="0012345678905")
    for typed in ("SCR-IP12-OLED", "0012345678905", "screen iphone"):
        assert await _products(client, h, typed) == {p["screen"]}, typed
        assert await _products(client, other, typed) == {twin}, typed
    assert await _products(client, other, "battery") == set()
    assert await _products(client, other, "4006381333931") == set()
