"""ASGI entrypoint: ``uvicorn aether.api.main:app``."""

from aether.api.app import create_app

app = create_app()
