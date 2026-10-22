"""Phase 5 regression tests: security and failure handling."""
import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import Mock, patch
import pytest
import tempfile

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


def test_sha256_file_rejects_unauthorized_boundary(tmp_path):
    from lifecycle import sha256_file
    fake_system_file = Path("/etc/hosts")
    if fake_system_file.is_file():
        with pytest.raises(LifecycleError, match="outside authorized path boundaries"):
            sha256_file(fake_system_file)


def test_sha256_file_rejects_symlink(tmp_path):
    from lifecycle import sha256_file
    target = tmp_path / "target.txt"
    target.write_text("hello")
    sym = tmp_path / "sym.txt"
    sym.symlink_to(target)
    with pytest.raises(LifecycleError, match="Symlink Target file refused"):
        sha256_file(sym)


def test_call_rejects_unauthorized_binary():
    from lifecycle import call
    with pytest.raises(LifecycleError, match="Unauthorized command binary"):
        call(["cat", "/dev/null"])


def test_call_rejects_illegal_control_characters():
    from lifecycle import call
    with pytest.raises(LifecycleError, match="illegal control characters"):
        call(["aws", "sts\x00malicious"])


def test_execute_plan_rejects_symlink_report(tmp_path):
    from lifecycle import execute_plan, LabConfig
    config = LabConfig(evidence_dir=tmp_path)
    real_report = tmp_path / "plan.json"
    real_report.write_text("{}")
    sym_report = tmp_path / "sym_plan.json"
    sym_report.symlink_to(real_report)
    with patch("lifecycle.account_guard"):
        with pytest.raises(LifecycleError, match="Symlink plan report refused"):
            execute_plan(config, str(sym_report), "CONFIRM", str(tmp_path / "vars.json"))


def test_execute_plan_rejects_plan_outside_evidence(tmp_path):
    from lifecycle import execute_plan, LabConfig
    config = LabConfig(evidence_dir=tmp_path)
    # Plan outside BASE directory
    outside_plan = Path(tempfile.gettempdir()) / "outside.tfplan"
    outside_plan.write_bytes(b"data")
    evidence_dir = ROOT / ".private-evidence/lifecycle/test-plan"
    evidence_dir.mkdir(parents=True, exist_ok=True)
    report_file = evidence_dir / "plan.json"
    report_file.write_text(json.dumps({
        "account": config.expected_account_id,
        "region": config.aws_region,
        "cluster": config.cluster_name,
        "kind": "rebuild",
        "tfvars_sha256": "fake",
        "plan": str(outside_plan),
        "plan_sha256": "fake",
        "state_lineage": "lineage",
        "state_serial": 1,
        "targets": []
    }))
    tfvars = tmp_path / "vars.json"
    tfvars.write_text("{}")
    tfvars.chmod(0o600)
    with patch("lifecycle.account_guard"), \
         patch("lifecycle.checked_inputs", return_value=tfvars), \
         patch("lifecycle.sha256_file", return_value="fake"), \
         patch("lifecycle.sha256_approved_file", return_value="fake"):
        with pytest.raises(LifecycleError, match="must exist under private lifecycle evidence"):
            execute_plan(config, str(report_file), f"REBUILD-{config.expected_account_id}", str(tfvars))


def test_restore_database_rejects_symlink_manifest(tmp_path):
    from recovery import restore_database
    from foundation import LabConfig
    config = LabConfig(evidence_dir=tmp_path)
    real = tmp_path / "manifest.json"
    real.write_text("{}")
    sym = tmp_path / "sym_manifest.json"
    sym.symlink_to(real)
    with patch("recovery.account_guard"):
        with pytest.raises(LifecycleError, match="Symlink recovery manifest refused"):
            restore_database(config, str(sym))


def test_restore_database_rejects_backup_outside_boundary(tmp_path):
    from recovery import restore_database
    from foundation import LabConfig
    config = LabConfig(evidence_dir=tmp_path)
    outside_backup = Path("/etc/shadow")
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({
        "account": config.expected_account_id,
        "cluster": config.cluster_name,
        "database": "app_db",
        "database_backup": str(outside_backup),
        "database_sha256": "fake",
        "created_utc": "2026-10-20T00:00:00Z"
    }))
    restore_test_file = tmp_path / "restore-test.json"
    restore_test_file.write_text(json.dumps({
        "status": "passed",
        "source_identity_verified": True,
        "restore_tested_to_database": True,
        "schema_verified": True,
        "runtime_permissions_verified": True,
        "database_cleanup_verified": True,
        "temporary_resources_cleanup_verified": True,
        "source_revision": "a" * 40,
        "database_sha256": "fake",
        "account": config.expected_account_id,
        "cluster": config.cluster_name,
        "database": "app_db",
        "created_utc": "2026-10-20T00:00:00Z",
    }))
    with patch("recovery.account_guard"), \
         patch("recovery.database_connection", return_value=({}, "host.rds.amazonaws.com", "app_db")), \
         patch("recovery.check_db_fresh"), \
         patch("recovery.age_identity"), \
         patch("lifecycle.validate_backup_manifest"), \
         patch("lifecycle.discover_latest_restore_test", return_value=restore_test_file), \
         patch("lifecycle.verify_backup_restore_provenance"):
        with pytest.raises(LifecycleError, match="outside authorized storage boundaries"):
            restore_database(config, str(manifest))

def test_call_rejects_executable_path_spoofing():
    from lifecycle import call

    with pytest.raises(LifecycleError, match="Unauthorized executable path"):
        call(["/tmp/aws", "sts", "get-caller-identity"])
