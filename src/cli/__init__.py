"""CLI package: interactive terminal entrypoint and Rich monitoring dashboard."""

__all__ = ["app"]


def __getattr__(name: str):
    if name == "app":
        from src.cli.main import app

        return app
    raise AttributeError(f"module '{__name__}' has no attribute '{name}'")
