"""Local development launcher: python -m couriercloud [--demo]."""

import argparse
import os
from wsgiref.simple_server import make_server

from .app import create_from_env


def main():
    parser = argparse.ArgumentParser(description="CourierCloud local development server")
    parser.add_argument("--demo", action="store_true", help="Seed fictional bookings and use local demo credentials")
    options = parser.parse_args()
    app = create_from_env(demo=options.demo)
    if app.production:
        app.store.close()
        parser.error("Use Gunicorn for production: gunicorn --config gunicorn.conf.py couriercloud.wsgi:application")
    host = "127.0.0.1" if options.demo else os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", "3000"))
    with make_server(host, port, app) as server:
        print(f"CourierCloud listening on http://{host}:{port}", flush=True)
        if options.demo:
            print("Local demo sign-in: dispatcher / courier-demo-2026", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            app.store.close()


if __name__ == "__main__":
    main()
