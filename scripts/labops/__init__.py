"""Minimal Stage 0 safety helpers for repository-local AWS preflight validation."""

__all__ = ["ConfigError", "PreflightError", "load_config", "run_preflight"]


def __getattr__(name):
    if name == "ConfigError":
        from .config import ConfigError
        return ConfigError
    if name == "PreflightError":
        from .preflight import PreflightError
        return PreflightError
    if name == "load_config":
        from .config import load_config
        return load_config
    if name == "run_preflight":
        from .preflight import run_preflight
        return run_preflight
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
