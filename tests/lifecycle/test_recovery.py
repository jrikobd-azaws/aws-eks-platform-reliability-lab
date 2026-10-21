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
    assert all(call.kwargs == {"deep": False} for call in m.validate.call_args_list)


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
    assert all(call.kwargs == {"deep": True} for call in m.validate.call_args_list)


def test_readiness_error_does_not_authorize_recovery(tmp_path):
    m = manager(tmp_path)
    m.validate = Mock(side_effect=LifecycleError("API state unknown"))
    action = Mock()

    with pytest.raises(LifecycleError, match="API state unknown"):
        m.run_stage("platform", action, execute=True)

    action.assert_not_called()


# Lifecycle Batch 2: fresh RDS must not create a platform/application deadlock.
def test_platform_readiness_excludes_stage3_application(tmp_path, monkeypatch):
    import recover_platform as rp
    calls = []
    m = rp.PlatformRecovery(
        LabConfig(evidence_dir=tmp_path),
        CheckpointStore(tmp_path / "platform.json"),
        executor=Mock(return_value=b""),
    )
    monkeypatch.setattr(rp, "validate_source_contract", lambda: None)
    monkeypatch.setattr(rp.PlatformRecovery, "verify_identity", lambda self, **kw: None)
    for name in ("argocd_ready", "gitops_dependencies_ready", "controllers_ready",
                 "observability_crds_ready", "grafana_secret_ready", "observability_ready"):
        monkeypatch.setattr(rp.PlatformRecovery, name,
                            lambda self, n=name: calls.append(n) or True)
    monkeypatch.setattr(rp.PlatformRecovery, "stage3_application_ready",
                        lambda self: (_ for _ in ()).throw(AssertionError("Stage3 called before DB")))
    assert rp.platform_recovery_state_ready(m.config) is True
    assert len(calls) == 6


def test_platform_recovery_never_runs_stage3_before_database(tmp_path, monkeypatch):
    import recover_platform as rp
    m = rp.PlatformRecovery(LabConfig(evidence_dir=tmp_path),
                            CheckpointStore(tmp_path / "platform.json"),
                            executor=Mock(return_value=b""))
    monkeypatch.setattr(rp, "validate_source_contract", lambda: None)
    monkeypatch.setattr(m, "verify_identity", Mock())
    monkeypatch.setattr(m, "_argocd_release", lambda: {"chart":"argo-cd-10.10.3", "revision":1,"status":"deployed"})
    monkeypatch.setattr(m, "run_stage", lambda name, ready, action: None)
    monkeypatch.setattr(m, "stage3_application_ready", lambda: (_ for _ in ()).throw(AssertionError("Stage3 before DB")))
    m.recover()
    assert not m.checkpoint.completed("stage3_application")


def test_database_failure_prevents_stage3_and_retry_revalidates(tmp_path):
    m = manager(tmp_path)
    m.account_guard = Mock()
    events=[]
    m.validate = Mock(side_effect=lambda stage, **kw: (
        True if stage in ("infrastructure", "platform") else
        False if stage == "database" else
        (_ for _ in ()).throw(AssertionError("verification before database"))
    ))
    m.restore_database = Mock(side_effect=RuntimeError("interrupted"))
    m.verify_environment = Mock()
    with pytest.raises(RuntimeError, match="interrupted"):
        m.resume(execute=True)
    assert m.checkpoint.load()["stages"]["database"]["status"] == "failed"
    m.verify_environment.assert_not_called()
    m.restore_database.assert_called_once()
    with pytest.raises(RuntimeError, match="interrupted"):
        m.resume(execute=True)
    assert m.restore_database.call_count == 2
    m.verify_environment.assert_not_called()


def test_final_verification_requires_fresh_sql_first(tmp_path, monkeypatch):
    import recover_platform as rp
    m=manager(tmp_path)
    monkeypatch.setattr(rp,"validate_recovered_database",lambda config: False)
    monkeypatch.setattr(rp,"validate_recovered_application",
                        lambda config: (_ for _ in ()).throw(AssertionError("Stage3 before DB")))
    assert m.verification_ready(deep=True) is False
    with pytest.raises(LifecycleError, match="Database schema not ready"):
        m.verify_environment()


def test_unknown_database_state_fails_closed(tmp_path, monkeypatch):
    import recover_platform as rp
    m=manager(tmp_path)
    def denied(config):
        raise LifecycleError("access denied")
    monkeypatch.setattr(rp,"validate_recovered_database",denied)
    with pytest.raises(LifecycleError,match="access denied"):
        m.verification_ready(deep=True)
