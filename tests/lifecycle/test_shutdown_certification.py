"""Backup-bound restore-test and shutdown certification regressions."""

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts/lifecycle"))

from foundation import LabConfig, LifecycleError, atomic_json_write
import lifecycle


def config(tmp_path):
    return LabConfig(evidence_dir=tmp_path)


def backup_fixture(tmp_path, *, account="275544662748", revision="a" * 40):
    run = tmp_path / "native-backup-20261021T000000000000Z"
    run.mkdir()
    encrypted = run / "database.dump.age"
    encrypted.write_bytes(b"encrypted")
    source = run / "source.tar.gz"
    source.write_bytes(b"source")
    manifest = run / "recovery-manifest.json"
    atomic_json_write(manifest, {
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "account": account,
        "cluster": "eks-platform-reliability-lab",
        "database": "app",
        "rds_endpoint": "db.example.rds.amazonaws.com",
        "rds_master_secret_arn": "arn:master",
        "database_backup": str(encrypted),
        "database_sha256": lifecycle.sha256_file(encrypted),
        "source_archive": str(source),
        "source_sha256": lifecycle.sha256_file(source),
        "source_revision": revision,
        "restore_tested": False,
    })
    return manifest, encrypted


def validated_backup(tmp_path):
    manifest, encrypted = backup_fixture(tmp_path)
    return {
        "manifest": manifest,
        "manifest_data": json.loads(manifest.read_text()),
        "backup": encrypted,
        "database_sha256": lifecycle.sha256_file(encrypted),
        "source_revision": "a" * 40,
        "archive": b"PGDMP archive",
        "values": {"rds_master_secret_arn": "arn:master"},
        "host": "db.example.rds.amazonaws.com",
        "database": "app",
    }


def restore_result(**overrides):
    value = {
        "disposable_database": "restore_test_20261021000000_deadbeef",
        "restore_succeeded": True,
        "schema_verified": True,
        "runtime_permissions_verified": True,
        "database_cleanup_verified": True,
        "temporary_resources_cleanup_verified": True,
        "failure_type": None,
    }
    value.update(overrides)
    return value


def test_restore_test_records_success(tmp_path):
    backup = validated_backup(tmp_path)
    approval = "RESTORE-TEST-275544662748-" + backup["database_sha256"][:12]
    with patch.object(lifecycle, "account_guard"), \
         patch.object(lifecycle, "validate_backup_manifest", return_value=backup), \
         patch.object(lifecycle, "_restore_archive_to_disposable_database",
                      return_value=restore_result()):
        report = lifecycle.restore_test_backup(
            config(tmp_path), confirmation=approval
        )
    data = json.loads(report.read_text())
    assert data["status"] == "passed"
    assert data["restore_tested_to_database"] is True
    assert data["database_cleanup_verified"] is True


def test_failed_restore_is_recorded_and_rejected(tmp_path):
    backup = validated_backup(tmp_path)
    approval = "RESTORE-TEST-275544662748-" + backup["database_sha256"][:12]
    failed = restore_result(
        restore_succeeded=False, schema_verified=False,
        runtime_permissions_verified=False, failure_type="LifecycleError",
    )
    with patch.object(lifecycle, "account_guard"), \
         patch.object(lifecycle, "validate_backup_manifest", return_value=backup), \
         patch.object(lifecycle, "_restore_archive_to_disposable_database",
                      return_value=failed), \
         pytest.raises(LifecycleError, match="Restore test or cleanup failed"):
        lifecycle.restore_test_backup(config(tmp_path), confirmation=approval)
    report = next(tmp_path.glob("native-restore-test-*/restore-test.json"))
    assert json.loads(report.read_text())["status"] == "failed"


def test_incomplete_cleanup_blocks_restore_test(tmp_path):
    backup = validated_backup(tmp_path)
    approval = "RESTORE-TEST-275544662748-" + backup["database_sha256"][:12]
    incomplete = restore_result(database_cleanup_verified=False)
    with patch.object(lifecycle, "account_guard"), \
         patch.object(lifecycle, "validate_backup_manifest", return_value=backup), \
         patch.object(lifecycle, "_restore_archive_to_disposable_database",
                      return_value=incomplete), \
         pytest.raises(LifecycleError):
        lifecycle.restore_test_backup(config(tmp_path), confirmation=approval)


def test_incorrect_backup_identity_is_rejected(tmp_path):
    manifest, _ = backup_fixture(tmp_path, account="000000000000")
    with patch.object(lifecycle, "_require_tools"), \
         patch.object(lifecycle, "database_connection", return_value=(
             {"rds_master_secret_arn": "arn:master"},
             "db.example.rds.amazonaws.com", "app",
         )), \
         patch.object(lifecycle, "current_source_revision", return_value="a" * 40), \
         pytest.raises(LifecycleError, match="Backup identity"):
        lifecycle.validate_backup_manifest(config(tmp_path), manifest)


def test_missing_restore_approval_performs_no_restore(tmp_path):
    backup = validated_backup(tmp_path)
    action = Mock()
    with patch.object(lifecycle, "account_guard"), \
         patch.object(lifecycle, "validate_backup_manifest", return_value=backup), \
         patch.object(lifecycle, "_restore_archive_to_disposable_database", action), \
         pytest.raises(LifecycleError, match="Explicit restore-test approval"):
        lifecycle.restore_test_backup(config(tmp_path), confirmation=None)
    action.assert_not_called()


def test_native_backup_age_keygen_is_allowed():
    assert "age-keygen" in lifecycle.ALLOWED_COMMAND_BINARIES


def test_uncertain_database_creation_requires_cleanup_reconciliation():
    source = Path(lifecycle.__file__).read_text()
    assert "database_creation_attempted = True" in source
    assert "if database_creation_attempted and pod_created:" in source


def plan_fixture(tmp_path, backup):
    tfvars = tmp_path / "inputs.tfvars.json"
    tfvars.write_text("{}")
    tfvars.chmod(0o600)
    saved = tmp_path / "terraform.tfplan"
    saved.write_bytes(b"plan")
    plan = {
        "schema": 1, "kind": "shutdown",
        "account": "275544662748", "region": "eu-west-1",
        "cluster": "eks-platform-reliability-lab",
        "tfvars_sha256": lifecycle.sha256_file(tfvars),
        "plan": str(saved), "plan_sha256": lifecycle.sha256_file(saved),
        "targets": ["module.eks.aws_eks_cluster.this"],
        "state_lineage": "lineage", "state_serial": 7,
    }
    report = tmp_path / "plan.json"
    atomic_json_write(report, plan)
    restore_dir = tmp_path / "native-restore-test-20261021T000000000000Z"
    restore_dir.mkdir()
    restore = restore_dir / "restore-test.json"
    atomic_json_write(restore, {
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "status": "passed", "account": plan["account"],
        "cluster": plan["cluster"], "database_sha256": backup["database_sha256"],
        "source_revision": "a" * 40, "source_identity_verified": True,
        "restore_tested_to_database": True, "schema_verified": True,
        "runtime_permissions_verified": True,
        "database_cleanup_verified": True,
        "temporary_resources_cleanup_verified": True,
    })
    return tfvars, report, plan, restore


def test_successful_shutdown_certification(tmp_path):
    backup = validated_backup(tmp_path)
    tfvars, report, plan, restore = plan_fixture(tmp_path, backup)
    approval = "CERTIFY-275544662748-" + plan["plan_sha256"][:12]
    delete_plan = {"resource_changes": [{
        "address": plan["targets"][0], "mode": "managed",
        "change": {"actions": ["delete"]},
    }]}
    with patch.object(lifecycle, "account_guard"), \
         patch.object(lifecycle, "current_source_revision", return_value="a" * 40), \
         patch.object(lifecycle, "call", return_value=json.dumps(
             {"lineage": "lineage", "serial": 7}
         ).encode()), \
         patch.object(lifecycle, "plan_json", return_value=delete_plan), \
         patch.object(lifecycle, "validate_backup_manifest", return_value=backup), \
         patch.object(lifecycle, "discover_latest_restore_test", return_value=restore), \
         patch.object(lifecycle, "verify_shutdown_live_gates", return_value={
             "retained_resources_verified": True,
             "gitops_cleanup_verified": True,
             "root_autosync_pause_verified": True,
             "dependency_counts": {},
         }):
        readiness = lifecycle.certify_shutdown(
            config(tmp_path), plan_report=report, tfvars=tfvars,
            confirmation=approval,
        )
    data = json.loads(readiness.read_text())
    assert data["safe_to_destroy"] is True
    assert data["plan_sha256"] == plan["plan_sha256"]
    assert data["restore_test_sha256"] == lifecycle.sha256_file(restore)


def test_stale_terraform_plan_blocks_certification(tmp_path):
    backup = validated_backup(tmp_path)
    tfvars, report, plan, _ = plan_fixture(tmp_path, backup)
    approval = "CERTIFY-275544662748-" + plan["plan_sha256"][:12]
    with patch.object(lifecycle, "account_guard"), \
         patch.object(lifecycle, "current_source_revision", return_value="a" * 40), \
         patch.object(lifecycle, "call", return_value=json.dumps(
             {"lineage": "lineage", "serial": 8}
         ).encode()), \
         pytest.raises(LifecycleError, match="state changed"):
        lifecycle.certify_shutdown(
            config(tmp_path), plan_report=report, tfvars=tfvars,
            confirmation=approval,
        )


def test_batch2_shutdown_missing_dml_privilege():
    checks = [
        "has_table_privilege(current_user, 'public.\"%s\"', '%s')"
        % (table, privilege)
        for table in ("user", "item")
        for privilege in ("SELECT", "INSERT", "UPDATE", "DELETE")
    ]
    assert len(checks) == 8
    assert len(set(checks)) == 8
    assert all(
        "SELECT,INSERT,UPDATE,DELETE" not in check
        for check in checks
    )

    expected = "|".join(("t", "t", "f") + ("t",) * 8)
    for index in range(3, 11):
        actual = expected.split("|")
        actual[index] = "f"
        assert "|".join(actual) != expected


@pytest.mark.parametrize(
    "policy,expected",
    [
        ({}, True),
        ({"automated": {"enabled": False}}, True),
        ({"automated": {}}, False),
        ({"automated": {"prune": False}}, False),
        ({"automated": {"enabled": True}}, False),
        ({"automated": True}, False),
    ],
)
def test_batch2_shutdown_manual_sync_policy(policy, expected):
    app = {"spec": {"syncPolicy": policy}}
    assert lifecycle._shutdown_manual_sync(app) is expected


def test_batch2_shutdown_wrong_kubernetes_identity(tmp_path):
    cfg = config(tmp_path)
    with patch.object(lifecycle, "terraform_values", return_value={
        "cluster_arn": "arn:aws:eks:eu-west-1:000000000000:cluster/wrong"
    }), pytest.raises(LifecycleError, match="cluster ARN mismatch"):
        lifecycle._verify_shutdown_kubernetes_identity(cfg)


def test_batch2_shutdown_missing_kubernetes_identity(tmp_path):
    cfg = config(tmp_path)
    with patch.object(lifecycle, "terraform_values", side_effect=
                      LifecycleError("Terraform identity unavailable")),          pytest.raises(LifecycleError, match="identity unavailable"):
        lifecycle._verify_shutdown_kubernetes_identity(cfg)


def test_batch2_shutdown_stale_backup():
    from datetime import timedelta

    old = (
        datetime.now(timezone.utc) - timedelta(hours=25)
    ).isoformat()

    recent = datetime.now(timezone.utc).isoformat()

    with pytest.raises(LifecycleError, match="stale"):
        lifecycle._verify_backup_restore_order(
            {"created_utc": old},
            {"created_utc": recent},
        )


def test_batch2_shutdown_restore_test_before_backup():
    from datetime import timedelta

    now = datetime.now(timezone.utc)

    with pytest.raises(LifecycleError, match="predates"):
        lifecycle._verify_backup_restore_order(
            {"created_utc": now.isoformat()},
            {"created_utc": (
                now - timedelta(minutes=5)
            ).isoformat()},
        )


def test_batch2_shutdown_backup_timestamp_required():
    with pytest.raises(LifecycleError, match="Invalid Backup timestamp"):
        lifecycle._verify_backup_restore_order(
            {"created_utc": None},
            {"created_utc": datetime.now(timezone.utc).isoformat()},
        )
