"""Bound request bodies and apply headers even to rejected/error responses."""

import hmac
import logging
import time
from collections import deque
from threading import Lock

from starlette.responses import JSONResponse

from .config import settings

HEADERS = {
    "Cache-Control": "no-store",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Content-Security-Policy": "default-src 'self'; script-src 'self' https://telegram.org; style-src 'self'; connect-src 'self'; img-src 'self' data: https://*.telegram.org; base-uri 'none'; form-action 'self'",
}


class PublicLimiter:
    """Process-local bound on unauthenticated traffic; no forwarded-IP trust."""

    def __init__(self, limit=240, capacity=4096, clock=time.monotonic):
        self.limit, self.capacity, self.clock = limit, capacity, clock
        self.entries = {}
        self.lock = Lock()

    def allow(self, key):
        now = self.clock()
        with self.lock:
            if key not in self.entries:
                self.entries = {k: v for k, v in self.entries.items() if v and v[-1] > now - 60}
                if len(self.entries) >= self.capacity:
                    return False
                self.entries[key] = deque()
            queue = self.entries[key]
            while queue and queue[0] <= now - 60:
                queue.popleft()
            if len(queue) >= self.limit:
                return False
            queue.append(now)
            return True


class HttpBoundary:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        started = False

        async def secure_send(message):
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
                names = {key.lower().encode() for key in HEADERS}
                message["headers"] = [
                    (k, v) for k, v in message.get("headers", []) if k.lower() not in names
                ]
                message["headers"] += [(k.lower().encode(), v.encode()) for k, v in HEADERS.items()]
            await send(message)

        async def reject(status, text):
            await JSONResponse({"error": text}, status)(scope, receive, secure_send)

        path = scope["path"]
        headers = dict(scope["headers"])
        # This capability is local monitoring only. Caddy additionally blocks it.
        if path == "/ready":
            secret = settings().health_token.get_secret_value()
            supplied = headers.get(b"x-health-token", b"")
            if len(secret) < 32 or not hmac.compare_digest(secret.encode(), supplied):
                return await reject(403, "Недоступно.")
        if path.startswith("/api/"):
            limiter = scope["app"].state.public_limiter
            peer = scope.get("client") or ("unknown", 0)
            group = "integration" if path.startswith("/api/integrations/") else "api"
            if not limiter.allow((peer[0], group)):
                return await reject(429, "Слишком много запросов. Повторите через минуту.")
        replay_receive = receive
        if scope["method"] == "POST":
            length = headers.get(b"content-length", b"")
            if not length.isdigit() or not 0 < int(length) <= 65536:
                return await reject(413, "Размер запроса превышен.")
            if headers.get(b"content-type", b"").split(b";")[0].strip() != b"application/json":
                return await reject(415, "Требуется JSON.")
            body = bytearray()
            while True:
                message = await receive()
                if message["type"] == "http.disconnect":
                    return
                body.extend(message.get("body", b""))
                if len(body) > 65536:
                    return await reject(413, "Размер запроса превышен.")
                if not message.get("more_body", False):
                    break
            delivered = False

            async def replay_receive():
                nonlocal delivered
                if not delivered:
                    delivered = True
                    return {"type": "http.request", "body": bytes(body), "more_body": False}
                return await receive()

        try:
            await self.app(scope, replay_receive, secure_send)
        except Exception as exc:  # noqa: BLE001 -- last HTTP boundary, never expose exception text
            if started:
                raise
            logging.getLogger("majlis").error("API failure: %s", type(exc).__name__)
            await reject(500, "Не удалось выполнить запрос. Повторите позже.")
