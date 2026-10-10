# JSON API

All API responses are JSON except the CSV export. Errors use `{ "error": "Message" }` and an appropriate HTTP status. Mutating requests require `Content-Type: application/json` and `X-Courier-Client: couriercloud`. Browser requests must use the same configured origin. JSON payloads are limited to 32 KiB.

## Authentication

`POST /api/login` accepts `{ "username": "dispatcher", "password": "your-secret" }`. Successful login sets an HttpOnly, SameSite=Strict session cookie with an eight-hour lifetime. Production also sets Secure. Eight failed attempts from one socket address block further attempts for 15 minutes; behind a reverse proxy this address can be shared by visitors.

`GET /api/me` returns the signed-in username and demo flag. `POST /api/logout` revokes the current session. All endpoints below require the session except public tracking and health.

| Method | Endpoint | Purpose / body |
| --- | --- | --- |
| GET | `/api/stats` | Dashboard totals and seven-day booking series |
| GET | `/api/shipments` | List all shipments |
| POST | `/api/shipments` | Book a shipment; body below |
| GET | `/api/shipments/:id` | Shipment details and private event history |
| POST | `/api/shipments/:id/assign` | `{ "driver_id": "DRV-…" }` |
| POST | `/api/shipments/:id/status` | `{ "status": "collected", "note": "Optional update" }` |
| GET | `/api/drivers` | List drivers and active job counts |
| POST | `/api/drivers` | `{ "name": "Alex", "vehicle": "Van", "phone": "+44…" }` |
| PATCH | `/api/drivers/:id` | `{ "active": false }` |
| GET | `/api/export` | Shipment CSV; charge column is integer pence |
| GET | `/api/track/:token` | Public progress; no personal details or private notes |
| GET | `/health` | Public health check |

## Booking example

```json
{
  "customer": "Example Studio",
  "email": "dispatch@example.com",
  "pickup": "1 Example Road, London",
  "destination": "2 Sample Street, London",
  "weight": 2.5,
  "service": "express",
  "notes": "Reception closes at 17:00"
}
```

The server validates the fields, calculates the sample charge and creates a random shipment reference and 128-bit tracking token. Service is `standard`, `express` or `same_day`; weight must be a numeric value greater than zero and at most 1,000 kg.

## Status rules

Assignment moves a booked shipment to `assigned`. Reassignment is allowed before collection. Status updates then follow `assigned` → `collected` → `in_transit` → `delivered`. Delivery requires a non-empty `recipient` field. Booked and assigned shipments may instead be cancelled. Terminal shipments cannot be reopened. Invalid transitions return HTTP 409 and do not change the record or its history.

A driver with active jobs cannot be set off duty. An off-duty driver cannot receive new jobs. Completed and cancelled jobs do not count towards active workload.

## Tracking links

The browser UI builds links in the form `https://your-domain/#track=<token>`. The token in the fragment is used to call the public progress endpoint. Anyone with the token can view that shipment's reference, service, current status and event timestamps. Customer data, addresses, recipient names, driver names and event notes are excluded. Treat each tracking link as a bearer credential and share it only with the intended customer.
