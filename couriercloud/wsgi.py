"""Gunicorn entry point; secrets are provided by environment variables."""

from .app import create_from_env

application = create_from_env()
