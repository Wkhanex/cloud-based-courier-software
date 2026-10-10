"""Run with python -m unittest discover -s tests -v."""
import io
import json
import tempfile
import threading
import unittest
import urllib.request
from pathlib import Path
from wsgiref.simple_server import make_server
from wsgiref.util import setup_testing_defaults
from couriercloud.app import CourierApp
from couriercloud.store import AppError, Store, quote_pence

BOOKING = {"customer": "Test Customer", "email": "test@example.com", "pickup": "1 Test Road, London",
           "destination": "2 Sample Street, London", "weight": 2.5, "service": "express", "notes": "Keep upright"}
DRIVER = {"name": "Test Driver", "vehicle": "Electric van", "phone": "+44 7700 900123"}


class AppTests(unittest.TestCase):
    def setUp(self):
        self.app = CourierApp(password="test-password-2026")
        self.cookie = ""
        status, headers, _ = self.request("/api/login", "POST", {"username": "dispatcher", "password": "test-password-2026"})
        self.assertEqual(status, 200)
        self.cookie = headers["Set-Cookie"].split(";")[0]

    def tearDown(self):
        self.app.store.close()

    def request(self, path, method="GET", values=None, authenticated=True, headers=None, raw=None):
        env = {}
        setup_testing_defaults(env)
        payload = raw if raw is not None else json.dumps(values).encode() if values is not None else b""
        env.update({"PATH_INFO": path, "REQUEST_METHOD": method, "REMOTE_ADDR": "127.0.0.1",
                    "HTTP_HOST": "localhost:3000", "wsgi.input": io.BytesIO(payload),
                    "CONTENT_LENGTH": str(len(payload)), "CONTENT_TYPE": "application/json",
                    "HTTP_X_COURIER_CLIENT": "couriercloud"})
        if authenticated:
            env["HTTP_COOKIE"] = self.cookie
        env.update(headers or {})
        result = {}

        def start_response(status, response_headers):
            result.update(status=int(status.split()[0]), headers=dict(response_headers))

        body = b"".join(self.app(env, start_response))
        if result["headers"].get("Content-Type", "").startswith("application/json") and body:
            body = json.loads(body)
        return result["status"], result["headers"], body

    def book(self):
        status, _, row = self.request("/api/shipments", "POST", BOOKING)
        self.assertEqual(status, 201)
        return row

    def test_protected_data_requires_session(self):
        for path in ("/api/shipments", "/api/stats", "/api/export"):
            self.assertEqual(self.request(path, authenticated=False)[0], 401)

    def test_login_rate_limit(self):
        for _ in range(8):
            self.assertEqual(self.request("/api/login", "POST", {"username": "dispatcher", "password": "wrong"})[0], 401)
        self.assertEqual(self.request("/api/login", "POST", {"username": "dispatcher", "password": "wrong"})[0], 429)

    def test_cookie_security_and_logout(self):
        _, headers, _ = self.request("/api/login", "POST", {"username": "dispatcher", "password": "test-password-2026"})
        self.assertIn("HttpOnly; SameSite=Strict", headers["Set-Cookie"])
        self.assertEqual(self.request("/api/logout", "POST", {})[0], 200)
        self.assertEqual(self.request("/api/me")[0], 401)

    def test_session_expiry(self):
        self.app.store.db.execute("UPDATE sessions SET expires_at=0")
        self.assertEqual(self.request("/api/me")[0], 401)

    def test_csrf_controls(self):
        self.assertEqual(self.request("/api/shipments", "POST", BOOKING, headers={"HTTP_ORIGIN": "https://other.example"})[0], 403)
        self.assertEqual(self.request("/api/shipments", "POST", BOOKING, headers={"HTTP_X_COURIER_CLIENT": "wrong"})[0], 403)

    def test_server_calculates_tariff(self):
        status, _, row = self.request("/api/shipments", "POST", {**BOOKING, "price_pence": 1})
        self.assertEqual(status, 201)
        self.assertEqual(row["price_pence"], 1700)
        self.assertEqual(row["status"], "booked")
        self.assertEqual(len(row["events"]), 1)

    def test_invalid_booking_fields(self):
        for values in ({"weight": 0}, {"weight": -1}, {"weight": 1001}, {"weight": 10 ** 400}, {"weight": True}, {"weight": "2"},
                       {"customer": ""}, {"email": "bad"}, {"service": "unknown"}, {"service": []}):
            with self.subTest(values=values):
                self.assertEqual(self.request("/api/shipments", "POST", {**BOOKING, **values})[0], 400)
        self.assertEqual(len(self.app.store.shipments()), 0)

    def test_full_delivery_flow(self):
        row = self.book()
        endpoint = f"/api/shipments/{row['id']}"
        self.assertEqual(self.request(endpoint + "/status", "POST", {"status": "delivered"})[0], 409)
        driver = self.app.store.add_driver(DRIVER)
        self.assertEqual(self.request(endpoint + "/assign", "POST", {"driver_id": driver["id"]})[0], 200)
        for status in ("collected", "in_transit"):
            self.assertEqual(self.request(endpoint + "/status", "POST", {"status": status})[0], 200)
        self.assertEqual(self.request(endpoint + "/status", "POST", {"status": "delivered"})[0], 400)
        self.assertEqual(self.app.store.shipment(row["id"])["status"], "in_transit")
        self.assertEqual(self.request(endpoint + "/status", "POST", {"status": "delivered", "recipient": "Test Recipient"})[0], 200)
        result = self.app.store.shipment(row["id"])
        self.assertEqual(len(result["events"]), 5)
        self.assertEqual(result["recipient"], "Test Recipient")

    def test_terminal_shipments_cannot_reopen(self):
        row = self.book()
        self.app.store.update_status(row["id"], {"status": "cancelled"})
        with self.assertRaises(AppError):
            self.app.store.update_status(row["id"], {"status": "booked"})
        driver = self.app.store.add_driver(DRIVER)
        with self.assertRaises(AppError):
            self.app.store.assign(row["id"], driver["id"])

    def test_driver_availability(self):
        row = self.book()
        driver = self.app.store.add_driver(DRIVER)
        self.app.store.assign(row["id"], driver["id"])
        with self.assertRaises(AppError):
            self.app.store.set_driver_active(driver["id"], False)
        self.app.store.update_status(row["id"], {"status": "cancelled"})
        self.app.store.set_driver_active(driver["id"], False)
        with self.assertRaises(AppError):
            self.app.store.assign(self.book()["id"], driver["id"])

    def test_invalid_assignment_is_atomic(self):
        row = self.book()
        for values in ({}, {"driver_id": "missing"}, {"driver_id": []}):
            self.assertEqual(self.request(f"/api/shipments/{row['id']}/assign", "POST", values)[0], 400)
        self.assertEqual(self.app.store.shipment(row["id"])["status"], "booked")
        self.assertEqual(len(self.app.store.shipment(row["id"])["events"]), 1)

    def test_public_tracking_privacy(self):
        row = self.book()
        driver = self.app.store.add_driver(DRIVER)
        self.app.store.assign(row["id"], driver["id"])
        status, _, result = self.request(f"/api/track/{row['tracking_token']}", authenticated=False)
        self.assertEqual(status, 200)
        self.assertEqual(result["status"], "assigned")
        for field in ("customer", "email", "pickup", "destination", "recipient", "notes", "driver_name", "tracking_token"):
            self.assertNotIn(field, result)
        self.assertNotIn("note", result["events"][1])
        self.assertEqual(self.request("/api/track/" + "0" * 32, authenticated=False)[0], 404)

    def test_parameterised_queries(self):
        customer = "Robert'); DROP TABLE shipments;--"
        status, _, row = self.request("/api/shipments", "POST", {**BOOKING, "customer": customer})
        self.assertEqual(status, 201)
        self.assertEqual(row["customer"], customer)
        self.assertEqual(len(self.app.store.shipments()), 1)

    def test_csv_formula_escaping(self):
        self.app.store.book({**BOOKING, "customer": '=HYPERLINK("https://example.com")'})
        status, headers, body = self.request("/api/export")
        self.assertEqual(status, 200)
        self.assertIn("text/csv", headers["Content-Type"])
        self.assertIn(b'"\'=HYPERLINK(""https://example.com"")"', body)
        self.assertIn(b'"1 Test Road, London"', body)

    def test_statistics_exclude_cancelled_value(self):
        row = self.book()
        self.assertEqual(self.app.store.stats()["booked_value_pence"], 1700)
        self.app.store.update_status(row["id"], {"status": "cancelled"})
        self.assertEqual(self.app.store.stats()["booked_value_pence"], 0)
        self.assertEqual(self.app.store.stats()["active"], 0)

    def test_database_persistence(self):
        with tempfile.TemporaryDirectory() as folder:
            database = Path(folder) / "test.sqlite"
            first = Store(database)
            row = first.book(BOOKING)
            first.close()
            second = Store(database)
            try:
                self.assertEqual(second.shipment(row["id"])["customer"], BOOKING["customer"])
            finally:
                second.close()

    def test_static_files_and_headers(self):
        status, headers, body = self.request("/")
        self.assertEqual(status, 200)
        self.assertIn(b"CourierCloud", body)
        self.assertIn("frame-ancestors 'none'", headers["Content-Security-Policy"])
        self.assertEqual(self.request("/health")[0], 200)
        self.assertEqual(self.request("/../../couriercloud/app.py")[0], 404)
        self.assertEqual(self.request("/", method="HEAD")[2], b"")

    def test_production_configuration(self):
        for values in ({"password": "short"}, {"password": "test-password-2026", "production": True},
                       {"password": "test-password-2026", "production": True, "origin": "https://example.com", "demo": True}):
            with self.assertRaises(ValueError):
                CourierApp(**values)
        cloud = CourierApp(password="test-password-2026", production=True, origin="https://example.com")
        try:
            self.assertIn("; Secure", cloud.cookie("token", 100))
        finally:
            cloud.store.close()

    def test_tariff_rounding(self):
        self.assertEqual(quote_pence(1, "standard"), 800)
        self.assertEqual(quote_pence(1.01, "standard"), 950)
        self.assertEqual(quote_pence(2.5, "same_day"), 2500)

    def test_demo_seed_idempotence_and_event_order(self):
        self.app.store.seed()
        self.app.store.seed()
        self.assertEqual(len(self.app.store.shipments()), 8)
        self.assertEqual(len(self.app.store.drivers()), 3)
        for row in self.app.store.shipments():
            events = self.app.store.shipment(row["id"])["events"]
            self.assertEqual([event["created_at"] for event in events], sorted(event["created_at"] for event in events))
            self.assertEqual(row["updated_at"], events[-1]["created_at"])

    def test_json_validation_and_size_limits(self):
        for raw in (b"[]", b"null", b"{broken", b'{"weight":NaN}'):
            self.assertEqual(self.request("/api/shipments", "POST", raw=raw)[0], 400)
        self.assertEqual(self.request("/api/shipments", "POST", raw=b"x" * 32769)[0], 413)
        self.assertEqual(self.request("/api/shipments", "POST", BOOKING, headers={"CONTENT_TYPE": "text/plain"})[0], 415)

    def test_real_http_server(self):
        with make_server("127.0.0.1", 0, self.app) as server:
            worker = threading.Thread(target=server.serve_forever, daemon=True)
            worker.start()
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{server.server_port}/health", timeout=3) as response:
                    self.assertEqual(json.load(response), {"status": "ok"})
            finally:
                server.shutdown()
                worker.join(timeout=3)


if __name__ == "__main__":
    unittest.main()
