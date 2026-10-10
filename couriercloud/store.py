"""SQLite persistence and transactional delivery workflow rules."""

import math
import re
import secrets
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

STATUSES = ("booked", "assigned", "collected", "in_transit", "delivered", "cancelled")


class AppError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def text_field(value, label, maximum=200):
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > maximum:
        raise AppError(f"{label} is required and must be at most {maximum} characters.")
    return value.strip()


def optional_text(value, maximum=500):
    return "" if value is None or value == "" else text_field(value, "Notes", maximum)


def quote_pence(weight, service):
    bases = {"standard": 800, "express": 1400, "same_day": 2200}
    if not isinstance(service, str) or service not in bases:
        raise AppError("Choose a valid service.")
    return bases[service] + max(0, math.ceil(weight - 1)) * 150


def timestamp(moment=None):
    return (moment or datetime.now(timezone.utc)).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def reference(prefix):
    return f"{prefix}-{secrets.token_hex(5).upper()}"


class Store:
    """One database connection; the application serialises access across threads."""

    def __init__(self, database=":memory:"):
        if str(database) != ":memory:":
            Path(database).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(database), isolation_level=None, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
            PRAGMA foreign_keys = ON;
            PRAGMA journal_mode = WAL;
            PRAGMA busy_timeout = 5000;
            CREATE TABLE IF NOT EXISTS drivers (
                id TEXT PRIMARY KEY, name TEXT NOT NULL, vehicle TEXT NOT NULL,
                phone TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0,1))
            );
            CREATE TABLE IF NOT EXISTS shipments (
                id TEXT PRIMARY KEY, tracking_token TEXT NOT NULL UNIQUE,
                customer TEXT NOT NULL, email TEXT NOT NULL, pickup TEXT NOT NULL,
                destination TEXT NOT NULL, weight REAL NOT NULL CHECK(weight > 0),
                service TEXT NOT NULL CHECK(service IN ('standard','express','same_day')),
                price_pence INTEGER NOT NULL CHECK(price_pence >= 0),
                status TEXT NOT NULL CHECK(status IN ('booked','assigned','collected','in_transit','delivered','cancelled')),
                driver_id TEXT REFERENCES drivers(id), notes TEXT NOT NULL,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL, recipient TEXT
            );
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                shipment_id TEXT NOT NULL REFERENCES shipments(id),
                status TEXT NOT NULL, note TEXT NOT NULL, created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS events_shipment ON events(shipment_id, id);
            CREATE TABLE IF NOT EXISTS sessions (
                token_hash TEXT PRIMARY KEY, expires_at REAL NOT NULL
            );
        """)

    def close(self):
        self.db.close()

    def transaction(self, operation):
        self.db.execute("BEGIN IMMEDIATE")
        try:
            result = operation()
            self.db.execute("COMMIT")
            return result
        except Exception:
            self.db.execute("ROLLBACK")
            raise

    def drivers(self):
        return [dict(row) for row in self.db.execute("""
            SELECT d.*, (SELECT COUNT(*) FROM shipments s WHERE s.driver_id = d.id
            AND s.status NOT IN ('delivered','cancelled')) AS active_jobs
            FROM drivers d ORDER BY name
        """)]

    def add_driver(self, values):
        driver = {
            "id": reference("DRV"),
            "name": text_field(values.get("name"), "Driver name", 80),
            "vehicle": text_field(values.get("vehicle"), "Vehicle", 80),
            "phone": text_field(values.get("phone"), "Phone", 30),
        }
        self.db.execute("INSERT INTO drivers(id,name,vehicle,phone) VALUES(:id,:name,:vehicle,:phone)", driver)
        return next(row for row in self.drivers() if row["id"] == driver["id"])

    def set_driver_active(self, driver_id, active):
        if not isinstance(active, bool):
            raise AppError("active must be true or false.")
        driver = next((row for row in self.drivers() if row["id"] == driver_id), None)
        if not driver:
            raise AppError("Driver not found.", 404)
        if not active and driver["active_jobs"]:
            raise AppError("Complete or reassign this driver’s jobs first.", 409)
        self.db.execute("UPDATE drivers SET active=? WHERE id=?", (int(active), driver_id))
        return next(row for row in self.drivers() if row["id"] == driver_id)

    def shipments(self):
        return [dict(row) for row in self.db.execute("""
            SELECT s.*, d.name AS driver_name FROM shipments s
            LEFT JOIN drivers d ON d.id=s.driver_id ORDER BY s.created_at DESC, s.id DESC
        """)]

    def shipment(self, shipment_id):
        row = self.db.execute("""
            SELECT s.*, d.name AS driver_name FROM shipments s
            LEFT JOIN drivers d ON d.id=s.driver_id WHERE s.id=?
        """, (shipment_id,)).fetchone()
        if not row:
            raise AppError("Shipment not found.", 404)
        result = dict(row)
        result["events"] = [dict(event) for event in self.db.execute(
            "SELECT status,note,created_at FROM events WHERE shipment_id=? ORDER BY id", (shipment_id,)
        )]
        return result

    def event(self, shipment_id, status, note, at=None):
        self.db.execute("INSERT INTO events(shipment_id,status,note,created_at) VALUES(?,?,?,?)",
                        (shipment_id, status, note, at or timestamp()))

    def book(self, values):
        weight = values.get("weight")
        if (isinstance(weight, bool) or not isinstance(weight, (int, float))
                or not 0 < weight <= 1000 or not math.isfinite(weight)):
            raise AppError("Weight must be greater than 0 and no more than 1,000 kg.")
        email = text_field(values.get("email"), "Customer email", 160)
        if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email):
            raise AppError("Enter a valid email address.")
        row = {
            "id": reference("CC"), "tracking_token": secrets.token_hex(16),
            "customer": text_field(values.get("customer"), "Customer name", 100),
            "email": email,
            "pickup": text_field(values.get("pickup"), "Pickup address", 240),
            "destination": text_field(values.get("destination"), "Delivery address", 240),
            "weight": weight, "service": values.get("service"),
            "price_pence": quote_pence(weight, values.get("service")),
            "notes": optional_text(values.get("notes")), "created_at": timestamp(),
        }

        def operation():
            self.db.execute("""
                INSERT INTO shipments(id,tracking_token,customer,email,pickup,destination,weight,
                service,price_pence,status,notes,created_at,updated_at)
                VALUES(:id,:tracking_token,:customer,:email,:pickup,:destination,:weight,
                :service,:price_pence,'booked',:notes,:created_at,:created_at)
            """, row)
            self.event(row["id"], "booked", "Booking received", row["created_at"])
            return self.shipment(row["id"])

        return self.transaction(operation)

    def assign(self, shipment_id, driver_id):
        if not isinstance(driver_id, str):
            raise AppError("Choose an active driver.")

        def operation():
            row = self.shipment(shipment_id)
            if row["status"] not in ("booked", "assigned"):
                raise AppError("Only booked or assigned jobs can be assigned.", 409)
            driver = self.db.execute("SELECT * FROM drivers WHERE id=? AND active=1", (driver_id,)).fetchone()
            if not driver:
                raise AppError("Choose an active driver.")
            at = timestamp()
            self.db.execute("UPDATE shipments SET driver_id=?,status='assigned',updated_at=? WHERE id=?",
                            (driver_id, at, shipment_id))
            self.event(shipment_id, "assigned", f"Assigned to {driver['name']}", at)
            return self.shipment(shipment_id)

        return self.transaction(operation)

    def update_status(self, shipment_id, values):
        allowed = {
            "booked": ("cancelled",), "assigned": ("collected", "cancelled"),
            "collected": ("in_transit",), "in_transit": ("delivered",),
            "delivered": (), "cancelled": (),
        }

        def operation():
            row = self.shipment(shipment_id)
            status = values.get("status")
            if status not in allowed[row["status"]]:
                raise AppError("This status change is not allowed.", 409)
            recipient = text_field(values.get("recipient"), "Recipient name", 100) if status == "delivered" else None
            note = optional_text(values.get("note")) or {
                "collected": "Parcel collected", "in_transit": "Parcel in transit",
                "delivered": "Delivery confirmed", "cancelled": "Booking cancelled",
            }[status]
            at = timestamp()
            self.db.execute("UPDATE shipments SET status=?,recipient=?,updated_at=? WHERE id=?",
                            (status, recipient, at, shipment_id))
            self.event(shipment_id, status, note, at)
            return self.shipment(shipment_id)

        return self.transaction(operation)

    def track(self, token):
        if not isinstance(token, str) or not re.fullmatch(r"[a-f0-9]{32}", token):
            raise AppError("Tracking link not found.", 404)
        row = self.db.execute("SELECT id,status,service,created_at,updated_at FROM shipments WHERE tracking_token=?",
                              (token,)).fetchone()
        if not row:
            raise AppError("Tracking link not found.", 404)
        result = dict(row)
        result["events"] = [dict(event) for event in self.db.execute(
            "SELECT status,created_at FROM events WHERE shipment_id=? ORDER BY id", (row["id"],)
        )]
        return result

    def stats(self):
        jobs = self.shipments()
        today = datetime.now(timezone.utc).date()
        terminal = ("delivered", "cancelled")
        return {
            "total": len(jobs), "active": sum(row["status"] not in terminal for row in jobs),
            "unassigned": sum(row["status"] == "booked" for row in jobs),
            "delivered": sum(row["status"] == "delivered" for row in jobs),
            "delivered_today": sum(row["status"] == "delivered" and row["updated_at"].startswith(str(today)) for row in jobs),
            "booked_value_pence": sum(row["price_pence"] for row in jobs if row["status"] != "cancelled"),
            "daily": [{"date": str(today - timedelta(days=6 - i)),
                       "count": sum(row["created_at"].startswith(str(today - timedelta(days=6 - i))) for row in jobs)}
                      for i in range(7)],
        }

    def create_session(self, token_hash, expires):
        self.db.execute("DELETE FROM sessions WHERE expires_at <= ?", (time.time(),))
        self.db.execute("INSERT INTO sessions(token_hash,expires_at) VALUES(?,?)", (token_hash, expires))

    def has_session(self, token_hash):
        return bool(self.db.execute("SELECT 1 FROM sessions WHERE token_hash=? AND expires_at>?",
                                    (token_hash, time.time())).fetchone())

    def delete_session(self, token_hash):
        self.db.execute("DELETE FROM sessions WHERE token_hash=?", (token_hash,))

    def seed(self):
        if self.db.execute("SELECT COUNT(*) FROM shipments").fetchone()[0]:
            return
        drivers = [self.add_driver({"name": name, "vehicle": vehicle, "phone": phone}) for name, vehicle, phone in (
            ("Alex Morgan", "Electric van · CC26 EV1", "+44 7700 900101"),
            ("Sam Taylor", "Cargo bike · BIKE-02", "+44 7700 900102"),
            ("Jordan Ellis", "Transit van · CC26 VAN", "+44 7700 900103"),
        )]
        origins = ("42 Clerkenwell Road, London EC1M 5PS", "18 Shoreditch High Street, London E1 6PG", "9 Southwark Street, London SE1 1RQ")
        destinations = ("14 King Street, London WC2E 8HN", "30 Paddington Street, London W1U 4HY", "7 Camden Road, London NW1 9LG")
        customers = ("North & Co", "Bloom Studio", "Paper House", "Atlas Supply", "Finch Coffee", "Moss Design", "Oak Workshop", "River Books")
        for i, customer in enumerate(customers):
            row = self.book({"customer": customer, "email": f"dispatch{i + 1}@example.com", "pickup": origins[i % 3],
                             "destination": destinations[i % 3], "weight": i + 0.5,
                             "service": ("standard", "express", "same_day")[i % 3], "notes": "Fictional demonstration booking."})
            if i > 1:
                row = self.assign(row["id"], drivers[i % 3]["id"])
            for threshold, status in ((2, "collected"), (3, "in_transit"), (5, "delivered")):
                if i > threshold:
                    row = self.update_status(row["id"], {"status": status, "recipient": "Demo recipient"})
            created = datetime.now(timezone.utc) - timedelta(days=i % 7, minutes=30)
            events = self.db.execute("SELECT id FROM events WHERE shipment_id=? ORDER BY id", (row["id"],)).fetchall()
            for index, event in enumerate(events):
                self.db.execute("UPDATE events SET created_at=? WHERE id=?", (timestamp(created + timedelta(minutes=index)), event["id"]))
            self.db.execute("UPDATE shipments SET created_at=?,updated_at=? WHERE id=?",
                            (timestamp(created), timestamp(created + timedelta(minutes=len(events) - 1)), row["id"]))
