"""Cap request body size (security phase 4, audit L4).

Uploads arrive as base64 inside JSON (media, feed attachments, recommendations) or as
multipart files (product import, invoice extraction), and each route checked its own size
only *after* the whole body had been read into memory — uvicorn imposes no limit, so one
request could hold gigabytes. This rejects an over-limit body with 413 before routing: from
Content-Length when present, otherwise as soon as the streamed bytes pass the cap.
"""
from starlette.responses import JSONResponse


class _TooLarge(Exception):
    pass


class BodySizeLimitMiddleware:
    def __init__(self, app, max_bytes: int):
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or self.max_bytes <= 0:
            return await self.app(scope, receive, send)
        for k, v in scope.get("headers") or ():
            if k == b"content-length":
                try:
                    if int(v) > self.max_bytes:
                        return await self._reject(scope, receive, send)
                except ValueError:
                    return await JSONResponse({"detail": "Bad Content-Length"}, 400)(scope, receive, send)
                break

        seen = 0
        started = False

        async def limited_receive():
            nonlocal seen
            msg = await receive()
            if msg["type"] == "http.request":
                seen += len(msg.get("body") or b"")
                if seen > self.max_bytes:
                    raise _TooLarge()
            return msg

        async def tracking_send(msg):
            nonlocal started
            if msg["type"] == "http.response.start":
                started = True
            await send(msg)

        try:
            await self.app(scope, limited_receive, tracking_send)
        except _TooLarge:
            if not started:
                await self._reject(scope, receive, send)

    async def _reject(self, scope, receive, send):
        mb = self.max_bytes / (1024 * 1024)
        await JSONResponse({"detail": f"Request too large (max {mb:g} MB)"}, 413)(scope, receive, send)
