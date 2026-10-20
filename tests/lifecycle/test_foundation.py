"""Unit tests for the Python lifecycle foundation."""

import json
import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts/lifecycle"))

from foundation import (
    CheckpointStore,
    CommandRunner,
    LabConfig,
    LifecycleError,
    atomic_json_write,
    redact,
    validate_stage_sequence,
    verify_account,
)


def test_config_from_environment():
    with patch.dict(os.environ, {
        "LAB_AWS_PROFILE": "test-profile",
        "LAB_AWS_REGION": "eu-west-1",
        "LAB_EXPECTED_ACCOUNT_ID": "123456789012",
        "LAB_CLUSTER_NAME": "test-cluster",
    }):
        config = LabConfig.from_environment()

    assert config.aws_profile == "test-profile"
    assert config.expected_account_id == "123456789012"


def test_reject_invalid_account():
    with patch.dict(os.environ, {
        "LAB_EXPECTED_ACCOUNT_ID": "invalid"
    }):
        with pytest.raises(LifecycleError):
            LabConfig.from_environment()


def test_checkpoint_roundtrip(tmp_path):
    store = CheckpointStore(tmp_path / "checkpoint.json")
    assert not store.completed("terraform")

    store.update("terraform", "running")
    store.update("terraform", "passed")

    assert store.completed("terraform")
    assert store.load()["stages"]["terraform"]["status"] == "passed"


def test_checkpoint_permissions(tmp_path):
    path = tmp_path / "checkpoint.json"
    atomic_json_write(path, {"schema_version": 1, "stages": {}})

    assert path.stat().st_mode & 0o777 == 0o600


def test_checkpoint_reject_invalid_state(tmp_path):
    store = CheckpointStore(tmp_path / "checkpoint.json")

    with pytest.raises(LifecycleError):
        store.update("terraform", "unknown")


def test_prerequisite_enforcement(tmp_path):
    store = CheckpointStore(tmp_path / "checkpoint.json")

    with pytest.raises(LifecycleError):
        validate_stage_sequence(store, ["backup", "terraform"])

    store.update("backup", "passed")
    store.update("terraform", "passed")

    validate_stage_sequence(store, ["backup", "terraform"])


def test_redaction():
    text = "password=top-secret-value"
    assert "top-secret-value" not in redact(
        text, ["top-secret-value"]
    )


def test_dry_run_never_executes():
    runner = CommandRunner(LabConfig(), dry_run=True)

    with patch("subprocess.run") as mocked:
        result = runner.run(["terraform", "plan"])

    mocked.assert_not_called()
    assert result.returncode == 0


def test_account_guard():
    runner = CommandRunner(LabConfig(), dry_run=True)

    with patch.object(runner, "run") as mocked:
        mocked.return_value.stdout = "123456789012"
        with pytest.raises(LifecycleError):
            verify_account(runner, "275544662748")


def test_command_failure_redaction():
    import subprocess

    runner = CommandRunner(LabConfig())

    error = subprocess.CalledProcessError(
        1,
        ["terraform", "plan"],
        stderr="credential=private-secret",
    )

    with patch("subprocess.run", side_effect=error):
        with pytest.raises(LifecycleError) as exc:
            runner.run(
                ["terraform", "plan"],
                secrets=["private-secret"],
            )

    assert "private-secret" not in str(exc.value)
