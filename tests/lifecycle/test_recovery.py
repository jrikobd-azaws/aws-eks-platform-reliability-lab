"""Checkpoint and recovery orchestration tests."""

import sys
from pathlib import Path
from unittest.mock import Mock

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts/lifecycle"))

from foundation import (
    CheckpointStore,
    LabConfig,
    LifecycleError,
)
from recovery import RecoveryManager, STAGES


def manager(tmp_path):
    config = LabConfig(
        evidence_dir=tmp_path,
    )

    runner = Mock()
    checkpoint = CheckpointStore(
        tmp_path / "checkpoint.json"
    )

    return RecoveryManager(config, runner, checkpoint)


def test_stage_order():
    assert STAGES == (
        "infrastructure",
        "platform",
        "database",
        "verification",
    )


def test_skip_revalidated_stage(tmp_path):
    m = manager(tmp_path)
    m.validate = Mock(return_value=True)
    action = Mock()

    m.run_stage(
        "infrastructure",
        action,
        execute=True,
    )

    action.assert_not_called()
    assert m.checkpoint.completed("infrastructure")


def test_execute_missing_stage(tmp_path):
    m = manager(tmp_path)
    m.validate = Mock(side_effect=[False, True])
    action = Mock()

    m.run_stage(
        "platform",
        action,
        execute=True,
    )

    action.assert_called_once()
    assert m.checkpoint.completed("platform")


def test_failed_stage_checkpoint(tmp_path):
    m = manager(tmp_path)
    m.validate = Mock(return_value=False)

    def broken():
        raise RuntimeError("Simulated recovery failure")

    with pytest.raises(RuntimeError):
        m.run_stage(
            "database",
            broken,
            execute=True,
        )

    assert (
        m.checkpoint.load()["stages"]["database"]["status"]
        == "failed"
    )


def test_preflight_does_not_execute(tmp_path):
    m = manager(tmp_path)
    m.account_guard = Mock()
    m.validate = Mock(return_value=False)

    m.restore_infrastructure = Mock()
    m.restore_platform = Mock()
    m.restore_database = Mock()

    report = m.resume(execute=False)

    assert all(
        value == "requires_recovery"
        for value in report["stages"].values()
    )

    m.restore_infrastructure.assert_not_called()
    m.restore_platform.assert_not_called()
    m.restore_database.assert_not_called()


def test_unapproved_infrastructure_apply_blocked(tmp_path):
    m = manager(tmp_path)

    with pytest.raises(LifecycleError):
        m.restore_infrastructure()


def test_invalid_stage_rejected(tmp_path):
    m = manager(tmp_path)

    with pytest.raises(LifecycleError):
        m.run_stage(
            "invalid",
            lambda: None,
            execute=True,
        )


def test_checkpoint_is_revalidated(tmp_path):
    m = manager(tmp_path)
    m.checkpoint.update("platform", "passed")

    m.validate = Mock(side_effect=[False, True])
    action = Mock()

    m.run_stage(
        "platform",
        action,
        execute=True,
    )

    action.assert_called_once()
