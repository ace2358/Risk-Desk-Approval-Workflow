"""Stable ASGI entry point; construction lives in the composition root."""

from workflow_engine.bootstrap import create_app


app = create_app()

__all__ = ["app", "create_app"]