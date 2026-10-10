"""Dependency-free Python WSGI API, authentication and static web UI."""

import csv
import hashlib
import hmac
import io
import json
import logging
import os
import re
import secrets
import threading
import time
from http import HTTPStatus
from http.cookies import CookieError, SimpleCookie
from pathlib import Path

from .store import AppError, Store

PUBLIC_DIR = Path(__file__).resolve().parent.parent / "public"
SESSION_SECONDS = 8 * 60 * 60


def token_hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


def password_key(password, salt):
    return hashlib.scrypt(password.encode(), salt=salt, n=16384, r=8, p=1, maxmem=64 * 1024 * 1024, dklen=64)


def session_token(environ):
    cookie = SimpleCookie()
    try:
        cookie.load(environ.get("HTTP_COOKIE", ""))
        token = cookie["courier_session"].value if "courier_session" in cookie else ""
        return token if re.fullmatch(r"[a-f0-9]{64}", token) else ""
    except CookieError:
        return ""


def json_body(environ):
    if environ.get("CONTENT_TYPE", "").split(";")[0].strip() != "application/json":
        raise AppError("Send application/json.", 415)
    try:
        length = int(environ.get("CONTENT_LENGTH") or "0")
    except ValueError:
        raise AppError("Invalid content length.") from None
    if length < 0 or length > 32768:
        raise AppError("Request is too large.", 413)
    try:
        raw = environ["wsgi.input"].read(length)
        values = json.loads(raw.decode("utf-8"), parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
        if not isinstance(values, dict):
            raise ValueError()
        return values
    except (ValueError, UnicodeDecodeError):
        raise AppError("Send a valid JSON object.") from None


class CourierApp:
    def __init__(self, database=":memory:", username="dispatcher", password=None,
                 production=False, origin="", demo=False):
        if not isinstance(password, str) or not 12 <= len(password) <= 256:
            raise ValueError("APP_PASSWORD must contain between 12 and 256 characters.")
        if production and not re.fullmatch(r"https://[A-Za-z0-9.-]+(?::[0-9]+)?", origin):
            raise ValueError("Production requires an HTTPS PUBLIC_ORIGIN without a trailing slash.")
        if demo and production:
            raise ValueError("Demo mode is for local development only.")
        self.username, self.production, self.origin, self.demo = username, production, origin, demo
        self.salt = secrets.token_bytes(16)
        self.expected_key = password_key(password, self.salt)
        self.store = Store(database)
        self.attempts = {}
        self.lock = threading.RLock()
        if demo:
            self.store.seed()

    def __call__(self, environ, start_response):
        headers = [
            ("X-Content-Type-Options", "nosniff"), ("X-Frame-Options", "DENY"),
            ("Referrer-Policy", "no-referrer"), ("Cache-Control", "no-store"),
            ("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"),
        ]
        if self.production:
            headers.append(("Strict-Transport-Security", "max-age=31536000"))
        try:
            with self.lock:
                status, body, extra = self.dispatch(environ)
            if isinstance(body, bytes):
                payload = body
            else:
                payload = json.dumps(body, ensure_ascii=False, allow_nan=False).encode("utf-8")
                extra.append(("Content-Type", "application/json; charset=utf-8"))
        except AppError as error:
            status = error.status
            payload = json.dumps({"error": str(error)}).encode()
            extra = [("Content-Type", "application/json")]
        except Exception:
            logging.exception("CourierCloud request failed")
            status, payload = 500, b'{"error":"An unexpected error occurred."}'
            extra = [("Content-Type", "application/json")]
        headers.extend(extra)
        headers.append(("Content-Length", str(len(payload))))
        start_response(f"{status} {HTTPStatus(status).phrase}", headers)
        return [b"" if environ.get("REQUEST_METHOD") == "HEAD" else payload]

    def dispatch(self, env):
        method, path = env.get("REQUEST_METHOD", "GET"), env.get("PATH_INFO", "/")
        if path == "/health" and method == "GET":
            return 200, {"status": "ok"}, []
        if not path.startswith("/api/"):
            if method not in ("GET", "HEAD"):
                raise AppError("Method not allowed.", 405)
            assets = {
                "/": ("index.html", "text/html"), "/app.js": ("app.js", "text/javascript"),
                "/styles.css": ("styles.css", "text/css"), "/favicon.svg": ("favicon.svg", "image/svg+xml"),
            }
            if path not in assets:
                raise AppError("Page not found.", 404)
            name, mime = assets[path]
            return 200, (PUBLIC_DIR / name).read_bytes(), [("Content-Type", f"{mime}; charset=utf-8")]
        if method not in ("GET", "HEAD"):
            if env.get("HTTP_X_COURIER_CLIENT") != "couriercloud":
                raise AppError("Missing client header.", 403)
            allowed_origin = self.origin or f"http://{env.get('HTTP_HOST', '')}"
            if env.get("HTTP_ORIGIN") and env["HTTP_ORIGIN"] != allowed_origin:
                raise AppError("Origin not allowed.", 403)
        if path == "/api/login" and method == "POST":
            return self.login(env)
        if path.startswith("/api/track/") and method == "GET":
            return 200, self.store.track(path.removeprefix("/api/track/")), []
        token = session_token(env)
        if not token or not self.store.has_session(token_hash(token)):
            raise AppError("Sign in to continue.", 401)
        if path == "/api/me" and method == "GET":
            return 200, {"username": self.username, "demo": self.demo}, []
        if path == "/api/logout" and method == "POST":
            self.store.delete_session(token_hash(token))
            return 200, {"ok": True}, [("Set-Cookie", self.cookie("", 0))]
        if path == "/api/stats" and method == "GET":
            return 200, self.store.stats(), []
        if path == "/api/drivers":
            if method == "GET":
                return 200, self.store.drivers(), []
            if method == "POST":
                return 201, self.store.add_driver(json_body(env)), []
        driver_match = re.fullmatch(r"/api/drivers/([A-Z0-9-]+)", path)
        if driver_match and method == "PATCH":
            return 200, self.store.set_driver_active(driver_match[1], json_body(env).get("active")), []
        if path == "/api/shipments":
            if method == "GET":
                return 200, self.store.shipments(), []
            if method == "POST":
                return 201, self.store.book(json_body(env)), []
        if path == "/api/export" and method == "GET":
            return self.export()
        shipment_match = re.fullmatch(r"/api/shipments/([A-Z0-9-]+)(?:/(assign|status))?", path)
        if shipment_match:
            shipment_id, action = shipment_match.groups()
            if not action and method == "GET":
                return 200, self.store.shipment(shipment_id), []
            if action == "assign" and method == "POST":
                return 200, self.store.assign(shipment_id, json_body(env).get("driver_id")), []
            if action == "status" and method == "POST":
                return 200, self.store.update_status(shipment_id, json_body(env)), []
        raise AppError("Endpoint not found.", 404)

    def cookie(self, token, lifetime):
        return (f"courier_session={token}; HttpOnly; SameSite=Strict; Path=/; Max-Age={lifetime}"
                + ("; Secure" if self.production else ""))

    def login(self, env):
        now, peer = time.time(), env.get("REMOTE_ADDR", "unknown")
        self.attempts = {key: value for key, value in self.attempts.items() if value["until"] > now}
        attempt = self.attempts.get(peer, {"count": 0, "until": now + 900})
        if attempt["count"] >= 8:
            raise AppError("Too many sign-in attempts. Try again in 15 minutes.", 429)
        values = json_body(env)
        password = values.get("password")
        if not isinstance(password, str) or len(password) > 256:
            raise AppError("Invalid credentials.", 401)
        valid = hmac.compare_digest(password_key(password, self.salt), self.expected_key)
        if values.get("username") != self.username or not valid:
            attempt["count"] += 1
            self.attempts[peer] = attempt
            raise AppError("Invalid credentials.", 401)
        self.attempts.pop(peer, None)
        token = secrets.token_hex(32)
        self.store.create_session(token_hash(token), now + SESSION_SECONDS)
        return 200, {"username": self.username, "demo": self.demo}, [("Set-Cookie", self.cookie(token, SESSION_SECONDS))]

    def export(self):
        columns = ("id", "customer", "email", "pickup", "destination", "weight", "service", "price_pence", "status", "driver_name", "created_at")
        stream = io.StringIO(newline="")
        writer = csv.writer(stream, quoting=csv.QUOTE_ALL)
        writer.writerow(columns)
        for row in self.store.shipments():
            cells = []
            for column in columns:
                value = "" if row[column] is None else str(row[column])
                cells.append("'" + value if re.match(r"^\s*[=+\-@]", value) else value)
            writer.writerow(cells)
        return 200, stream.getvalue().encode("utf-8"), [
            ("Content-Type", "text/csv; charset=utf-8"),
            ("Content-Disposition", 'attachment; filename="couriercloud-shipments.csv"'),
        ]


def load_env(path=".env"):
    """Read simple KEY=value settings; process environment always takes precedence."""
    file = Path(path)
    if not file.is_file():
        return
    for line in file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key, separator, value = line.partition("=")
        if separator and re.fullmatch(r"[A-Z][A-Z0-9_]*", key.strip()):
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                value = value[1:-1]
            os.environ.setdefault(key.strip(), value)


def create_from_env(demo=False):
    if not demo:
        load_env()
    return CourierApp(
        database="./data/demo.sqlite" if demo else os.environ.get("DATABASE_PATH", "./data/couriercloud.sqlite"),
        username="dispatcher" if demo else os.environ.get("APP_USERNAME", "dispatcher"),
        password="courier-demo-2026" if demo else os.environ.get("APP_PASSWORD"),
        production=os.environ.get("APP_ENV") == "production",
        origin="" if demo else os.environ.get("PUBLIC_ORIGIN", ""), demo=demo,
    )
