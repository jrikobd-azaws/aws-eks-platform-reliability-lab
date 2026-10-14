"""Load and validate the non-sensitive lab configuration."""

from __future__ import annotations

import os
import re
from typing import Mapping

REQUIRED_KEYS = ("EXPECTED_ACCOUNT_ID", "EXPECTED_AWS_REGION")
OPTIONAL_KEYS = (
    "AWS_PROFILE",
    "AWS_REGION",
    "CLUSTER_NAME",
    "GITHUB_REPOSITORY",
    "KUBERNETES_CONTEXT",
)

_ACCOUNT_RE = re.compile(r"^\d{12}$")
_REGION_RE = re.compile(r"^[a-z]{2}(-gov)?-[a-z0-9-]+-\d+$")


class ConfigError(ValueError):
    """Raised when the lab configuration is missing or malformed."""


def _normalise(value):
    if value is None:
        return ""
    return str(value).strip()


def _validate_account_id(account_id: str) -> str:
    if not account_id:
        raise ConfigError("Missing EXPECTED_ACCOUNT_ID in configuration.")
    if not _ACCOUNT_RE.fullmatch(account_id):
        raise ConfigError(
            "EXPECTED_ACCOUNT_ID must be a valid 12-digit AWS account ID; "
            f"received {account_id!r}."
        )
    return account_id


def _validate_region(region: str) -> str:
    if not region:
        raise ConfigError("Missing EXPECTED_AWS_REGION in configuration.")
    if not _REGION_RE.fullmatch(region):
        raise ConfigError(
            "EXPECTED_AWS_REGION must be a valid AWS region such as us-east-1; "
            f"received {region!r}."
        )
    return region


def load_config(overrides: Mapping[str, str] | None = None, env: Mapping[str, str] | None = None) -> dict[str, str]:
    """Load and validate the project configuration from environment variables.

    This module intentionally stores only non-sensitive configuration. No AWS credentials,
    kubeconfig secrets, or GitHub tokens are loaded here.
    """

    source = dict(os.environ if env is None else env)
    if overrides:
        source.update({str(key): str(value) for key, value in overrides.items()})

    config: dict[str, str] = {}
    for key in REQUIRED_KEYS:
        value = _normalise(source.get(key))
        if key == "EXPECTED_ACCOUNT_ID":
            config[key] = _validate_account_id(value)
        elif key == "EXPECTED_AWS_REGION":
            config[key] = _validate_region(value)

    for key in OPTIONAL_KEYS:
        value = _normalise(source.get(key))
        if value:
            config[key] = value

    return config
