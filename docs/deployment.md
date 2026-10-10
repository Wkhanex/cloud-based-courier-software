# Cloud deployment

Deploy the Docker image to a service that provides a **persistent disk**, HTTPS termination and **one running instance**. The application stores its database on that disk; an ephemeral container filesystem will lose data during replacement.

1. Build the image with `docker build -t couriercloud .`.
2. Mount a persistent writable volume at `/app/data`, owned by the container's `courier` user (UID 1000).
3. Set `APP_USERNAME`, a unique `APP_PASSWORD`, `APP_ENV=production`, `HOST=0.0.0.0`, and `PUBLIC_ORIGIN=https://your-exact-domain` with no trailing slash. Use platform secret settings for the password.
4. Route HTTPS traffic through the platform's reverse proxy to the container's configured `PORT` (default 3000).
5. Use `GET /health` for platform health checks.
6. Sign in, add your drivers and create a test shipment. Verify its tracking link and persisted data after a controlled restart.

The Docker image installs Gunicorn and runs as a non-root user. It starts one worker with four threads; application requests share a lock around the SQLite connection. The supplied Compose file is for local development; it overrides production mode so local HTTP works. Do not use those development settings for an internet-facing deployment.

## Persistence and backups

SQLite uses WAL mode. Keep the database, its `-wal` file and its `-shm` file on the same persistent volume. Use SQLite's backup tooling for an online backup, or stop the app before taking a complete consistent filesystem snapshot. Test restoration before relying on backups. Do not copy only the main database file from a running instance.

Do not point several containers at one SQLite file or scale the service to multiple replicas. For that requirement, replace the storage layer with a network database and add database migrations and shared rate limiting.

## Operational limits

Login credentials are configured through environment variables and verified against an in-memory scrypt-derived key. Sessions are stored as token hashes in SQLite. Password rotation does not revoke existing sessions automatically; stop the app, remove rows from `sessions`, change the secret and restart when rotation requires immediate revocation.

Rate limiting is process-local and keyed to the TCP peer address. A reverse proxy may cause several users to share that limit. Use edge rate limiting and a deliberately configured trusted-proxy identity strategy if deploying to a wider audience.

The `/health` endpoint verifies that the process responds; it does not exercise database read/write capability. Monitor application errors and disk capacity separately. The Docker build and deployment need to be verified on your chosen host; the repository's tests do not simulate that platform.

## Serve without Docker

After setting the environment variables, install the serving dependency and start the WSGI application:

```bash
python -m pip install -r requirements.txt
gunicorn --config gunicorn.conf.py couriercloud.wsgi:application
```

The `.env` loader accepts simple `KEY=value` settings and optional quotes. Existing process environment variables take precedence. It does not expand variables or run shell expressions. Local verification covers the Python tests and an HTTP smoke test; the Docker image needs verification on your chosen host.
