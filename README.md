# CourierCloud

Cloud-based courier software for booking shipments, assigning drivers and tracking deliveries through a shared dispatch workspace.

This runnable starter includes a responsive web dashboard, a JSON API and a persistent SQLite database. The backend is written in Python 3.12+ using the standard library. HTML, CSS and vanilla JavaScript provide the browser interface. Gunicorn serves the app in cloud deployments.

## What it does

- **Dispatch dashboard:** active deliveries, completed deliveries, unassigned bookings, booking values and a seven-day activity chart.
- **Shipment booking:** customer details, pickup and destination addresses, parcel weight, service level and private notes.
- **Driver management:** add drivers, assign or reassign jobs before collection, and set availability.
- **Delivery progress:** booked → assigned → collected → in transit → delivered, with a timestamped event history and named recipient confirmation.
- **Customer tracking:** share an unguessable tracking link that shows progress without exposing addresses, email, driver details or private notes.
- **Search and export:** search bookings, filter by status and download a CSV report.
- **Cloud packaging:** Docker image, persistent volume configuration, health endpoint and GitHub Actions checks.

## Run the local demo

Install Python 3.12 or newer, then:

```bash
python -m couriercloud --demo
```

Open **http://127.0.0.1:3000** and sign in:

| Field | Demo value |
| --- | --- |
| Username | `dispatcher` |
| Password | `courier-demo-2026` |

The demo seeds eight fictional shipments and three drivers. Changes persist in `data/demo.sqlite`; remove that database while the app is stopped to reset the demo. Demo mode binds to the loopback address and refuses to start in production mode.

## Run your own workspace

```bash
cp .env.example .env
```

Set `APP_PASSWORD` to a unique password of 12–256 characters and then run:

```bash
python -m couriercloud
```

Use the origin configured in `.env`, normally **http://localhost:3000**. Your new workspace starts empty. Add a driver, book a shipment, open its details and assign it. Move the shipment through collection and transit before confirming delivery.

The local app, demo and tests need no third-party packages. Cloud hosting uses Gunicorn from `requirements.txt`. Python’s built-in `sqlite3` module handles persistence.

## Run with Docker

After setting `APP_PASSWORD` in `.env`:

```bash
docker compose up --build
```

Open **http://localhost:3000**. The Compose file is configured for local development and keeps data in a named volume. Cloud deployment uses the production settings described in [docs/deployment.md](docs/deployment.md).

## Configuration

| Variable | Purpose | Default |
| --- | --- | --- |
| `APP_USERNAME` | Dispatcher login | `dispatcher` |
| `APP_PASSWORD` | Required login secret | None |
| `PORT` | HTTP listener port | `3000` |
| `HOST` | Listener address | `127.0.0.1`; Docker uses `0.0.0.0` |
| `DATABASE_PATH` | Persistent SQLite file | `./data/couriercloud.sqlite` |
| `PUBLIC_ORIGIN` | Exact browser origin used for mutation checks | Required HTTPS origin in production |
| `APP_ENV` | Production enables Secure cookies and HSTS | Development |

## Development

```bash
python -m compileall -q couriercloud tests
python -m unittest discover -s tests -v
```

The tests exercise authentication, session expiry, CSRF controls, tariffs, booking validation, dispatch rules, delivery confirmation, transaction rollback, tracking privacy, CSV export and database persistence.

```text
couriercloud/        Python WSGI API, authentication and database operations
public/              Responsive web UI, styles and browser behaviour
tests/               Integration and persistence tests
docs/                API and deployment documentation
.github/workflows/   Automated checks
```

See [docs/api.md](docs/api.md) for endpoint details and [docs/architecture.md](docs/architecture.md) for the data model.

## Scope

CourierCloud is a functional single-team starter. Delivery updates are entered by a dispatcher; tracking reads the stored event history. It does not include live GPS, route optimisation, carrier integrations, email/SMS sending, billing, signature/photo evidence or separate driver/customer accounts.

Charges use a sample tariff in GBP, stored as integer pence, excluding VAT: standard £8, express £14 and same day £22 for the first kg, plus £1.50 per additional started kg. These values demonstrate booking calculations and are not invoiced revenue. Dashboard dates use UTC.

The app supports one dispatcher credential and one server instance. The local launcher uses Python’s development WSGI server; use Gunicorn for cloud serving. Add individual accounts, role permissions, recovery flows and an operational backup plan before adapting it for a live courier business. SQLite needs a persistent local disk and is not configured for multiple cloud replicas.

## Licence

MIT — see [LICENSE](LICENSE).
