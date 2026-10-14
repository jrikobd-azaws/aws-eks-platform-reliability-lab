"""Read-only AWS preflight checks for Stage 0 safety validation."""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Callable, Mapping

from botocore.exceptions import BotoCoreError, ClientError

from .config import ConfigError, load_config

ALLOWED_MODES = {"preview", "check"}


class PreflightError(RuntimeError):
    """Raised when a required validation fails."""


def _get_session_region(session) -> str | None:
    return (
        getattr(session, "region_name", None)
        or os.environ.get("AWS_REGION")
        or os.environ.get("AWS_DEFAULT_REGION")
    )


def _default_session_factory(config: Mapping[str, str]):
    import boto3

    profile_name = config.get("AWS_PROFILE") or None
    if profile_name:
        return boto3.session.Session(profile_name=profile_name)
    return boto3.session.Session()


def run_preflight(
    config: Mapping[str, str] | None,
    *,
    mode: str = "preview",
    session_factory: Callable[[], object] | None = None,
) -> dict:
    """Validate config and, in check mode, read-only AWS identity and region checks."""

    if mode not in ALLOWED_MODES:
        raise PreflightError(f"Unsupported preflight mode {mode!r}; expected one of {sorted(ALLOWED_MODES)}.")

    if config is None:
        config = load_config()
    else:
        config = load_config(overrides=config)

    result: dict = {
        "success": False,
        "mode": mode,
        "account": config.get("EXPECTED_ACCOUNT_ID"),
        "region": config.get("EXPECTED_AWS_REGION"),
        "checks": [],
    }

    if mode == "preview":
        result["success"] = True
        result["checks"] = [
            "Configuration validated locally.",
            "Preview mode does not perform external API calls.",
            "Expected AWS account and region were checked in configuration only.",
        ]
        return result

    if session_factory is None:
        session_factory = lambda: _default_session_factory(config)

    try:
        session = session_factory()
        sts_client = session.client("sts")
        identity = sts_client.get_caller_identity()
    except (ClientError, BotoCoreError, Exception) as exc:  # pragma: no cover - defensive branch for mocked failures
        raise PreflightError(f"Failed to read AWS identity via STS GetCallerIdentity: {exc}") from exc

    actual_account = str(identity.get("Account", "")).strip()
    if not actual_account:
        raise PreflightError("Failed to read AWS account from STS GetCallerIdentity response.")

    expected_account = config.get("EXPECTED_ACCOUNT_ID")
    if actual_account != expected_account:
        raise PreflightError(
            "Expected AWS account "
            f"{expected_account} but STS reported {actual_account}."
        )

    actual_region = _get_session_region(session)
    expected_region = config.get("EXPECTED_AWS_REGION")
    if not actual_region:
        raise PreflightError("AWS session region is not configured; cannot validate the expected deployment region.")
    if actual_region != expected_region:
        raise PreflightError(
            "Expected AWS session region "
            f"{expected_region} but the current session reports {actual_region}."
        )

    result["success"] = True
    result["account"] = actual_account
    result["region"] = actual_region
    result["principal"] = identity.get("Arn")
    result["checks"] = [
        "STS GetCallerIdentity succeeded.",
        "AWS account matches the expected configuration.",
        "The active AWS session region matches the expected deployment region.",
    ]
    return result


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Read-only AWS preflight validation for the Stage 0 lab safety foundation.")
    mode_group = parser.add_mutually_exclusive_group()
    mode_group.add_argument("--preview", action="store_true", help="Validate local configuration and describe checks without external API calls.")
    mode_group.add_argument("--check", action="store_true", help="Run read-only AWS STS and region checks.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    mode = "check" if args.check else "preview"
    try:
        config = load_config()
        result = run_preflight(config, mode=mode)
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0 if result["success"] else 1
    except (ConfigError, PreflightError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
