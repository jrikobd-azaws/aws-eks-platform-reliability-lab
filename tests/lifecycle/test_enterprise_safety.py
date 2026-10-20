"""Phase 5 regression tests: security and failure handling."""
import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import Mock, patch
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts/lifecycle"))
from foundation import CheckpointStore, CommandRunner, LabConfig, LifecycleError, atomic_json_write
from lifecycle import LifecycleManager, classify_resources, validate_destroy_plan
from recovery import RecoveryManager


def test_redacts_subprocess_error():
    runner = CommandRunner(LabConfig())
    error = subprocess.CalledProcessError(1, ["terraform", "plan"], stderr="password=private-value")
    with patch("subprocess.run", side_effect=error):
        with pytest.raises(LifecycleError) as exc:
            runner.run(["terraform", "plan"], secrets=["private-value"])
    assert "private-value" not in str(exc.value)


def test_timeout_fails_closed():
    runner = CommandRunner(LabConfig())
    with patch("subprocess.run", side_effect=subprocess.TimeoutExpired(["terraform"], 1)):
        with pytest.raises(LifecycleError):
            runner.run(["terraform", "plan"], timeout=1)


def test_checkpoint_survives_reload(tmp_path):
    path = tmp_path / "checkpoint.json"
    CheckpointStore(path).update("terraform", "passed")
    assert CheckpointStore(path).completed("terraform")


def test_corrupt_checkpoint_rejected(tmp_path):
    path = tmp_path / "checkpoint.json"
    path.write_text("{invalid")
    with pytest.raises(json.JSONDecodeError):
        CheckpointStore(path).load()


def test_protected_ecr_delete_rejected():
    address = "module.eks.aws_eks_cluster.this"
    plan = {"resource_changes": [
        {"address": address, "mode": "managed", "change": {"actions": ["delete"]}},
        {"address": 'aws_ecr_repository.stage3["backend"]', "mode": "managed", "change": {"actions": ["delete"]}},
    ]}
    with pytest.raises(LifecycleError):
        validate_destroy_plan(plan, [address])


def test_wrong_confirmation_never_applies(tmp_path):
    runner = Mock()
    runner.run.return_value.stdout = "275544662748"
    manager = LifecycleManager(LabConfig(evidence_dir=tmp_path), runner, CheckpointStore(tmp_path / "cp.json"))
    with pytest.raises(LifecycleError):
        manager.execute_shutdown(confirmation="WRONG")
    assert runner.run.call_count == 1


def test_tampered_plan_never_applies(tmp_path):
    runner = Mock()
    runner.run.return_value.stdout = "275544662748"
    manager = LifecycleManager(LabConfig(evidence_dir=tmp_path), runner, CheckpointStore(tmp_path / "cp.json"))
    plan = tmp_path / "plan.tfplan"
    plan.write_bytes(b"tampered")
    atomic_json_write(tmp_path / "python-shutdown-latest.json", {
        "plan": str(plan), "plan_sha256": "0" * 64,
        "targets": ["module.eks.aws_eks_cluster.this"],
    })
    with pytest.raises(LifecycleError):
        manager.execute_shutdown(confirmation="DESTROY-275544662748")
    assert runner.run.call_count == 1


def test_unknown_resource_rejected():
    with pytest.raises(LifecycleError):
        classify_resources(["module.eks.aws_eks_cluster.this", "aws_iam_role.unknown"])


def test_failed_recovery_checkpoint(tmp_path):
    manager = RecoveryManager(LabConfig(evidence_dir=tmp_path), Mock(), CheckpointStore(tmp_path / "cp.json"))
    manager.validate = Mock(return_value=False)
    def fail():
        raise RuntimeError("simulated")
    with pytest.raises(RuntimeError):
        manager.run_stage("database", fail, execute=True)
    assert manager.checkpoint.load()["stages"]["database"]["status"] == "failed"


def test_preflight_never_restores(tmp_path):
    manager = RecoveryManager(LabConfig(evidence_dir=tmp_path), Mock(), CheckpointStore(tmp_path / "cp.json"))
    manager.account_guard = Mock()
    manager.validate = Mock(return_value=False)
    manager.restore_infrastructure = Mock()
    manager.restore_platform = Mock()
    manager.restore_database = Mock()
    manager.resume(execute=False)
    manager.restore_infrastructure.assert_not_called()
    manager.restore_platform.assert_not_called()
    manager.restore_database.assert_not_called()


def test_deployment_module_has_no_apply_or_destroy_invocation():
    source = (ROOT / "scripts/lifecycle/deployment.py").read_text()
    assert "terraform apply" not in source
    assert "terraform destroy" not in source


def test_public_example_has_no_private_recovery_reference():
    source = (ROOT / "config/lab.example.json").read_text()
    assert ".private-evidence" not in source
    assert "rds!db-" not in source
    assert "sha256:a2ef52" not in source


def test_checkpoint_malformed_structure_rejected(tmp_path):
    path = tmp_path / "checkpoint.json"
    path.write_text(json.dumps({"schema_version": 1, "stages": "not-a-dict"}))
    with pytest.raises(LifecycleError, match="Invalid checkpoint stages"):
        CheckpointStore(path).load()


def test_checkpoint_invalid_stage_entry_rejected(tmp_path):
    path = tmp_path / "checkpoint.json"
    path.write_text(json.dumps({
        "schema_version": 1,
        "stages": {"infrastructure": "invalid-entry"}
    }))
    with pytest.raises(LifecycleError, match="Invalid stage info structure"):
        CheckpointStore(path).load()


def test_checkpoint_invalid_stage_status_rejected(tmp_path):
    path = tmp_path / "checkpoint.json"
    path.write_text(json.dumps({
        "schema_version": 1,
        "stages": {"infrastructure": {"status": "bogus_status"}}
    }))
    with pytest.raises(LifecycleError, match="Invalid status"):
        CheckpointStore(path).load()


def test_command_runner_redacts_stdout_when_stderr_empty():
    runner = CommandRunner(LabConfig())
    error = subprocess.CalledProcessError(1, ["terraform", "plan"], output="token=secret-token", stderr="")
    with patch("subprocess.run", side_effect=error):
        with pytest.raises(LifecycleError) as exc:
            runner.run(["terraform", "plan"], secrets=["secret-token"])
    assert "secret-token" not in str(exc.value)
    assert "[REDACTED]" in str(exc.value)


def test_cli_error_does_not_expose_exception_secrets():
    from labctl import blocked_error_message

    secret = "super-sensitive-example-token"
    error = RuntimeError(
        "database connection failed: password=" + secret
    )

    message = blocked_error_message(error)

    assert message.startswith("BLOCKED: RuntimeError")
    assert secret not in message
    assert "password=" not in message
    assert str(error) not in message
