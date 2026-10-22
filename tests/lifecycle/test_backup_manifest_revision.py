"""Offline regression tests for backup manifest source-revision policy."""

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(
    0, str(Path(__file__).resolve().parents[2] / "scripts/lifecycle")
)

import lifecycle
from foundation import LifecycleError


@pytest.fixture
def manifest_case(tmp_path, monkeypatch):
    controller = "b" * 40
    backup_source = "a" * 40

    config = SimpleNamespace(
        expected_account_id="275544662748",
        aws_region="eu-west-1",
        cluster_name="eks-platform-reliability-lab",
        evidence_dir=tmp_path,
    )

    historical_secret = "historical-secret"
    current_secret = "current-secret"

    manifest_data = {
        "account": config.expected_account_id,
        "cluster": config.cluster_name,
        "database": "app",
        "rds_endpoint": "db.example.internal",
        "source_revision": backup_source,
        "rds_master_secret_arn": historical_secret,
        "database_backup": str(tmp_path / "backup.age"),
        "source_archive": str(tmp_path / "source.tar.gz"),
        "database_sha256": "database-checksum",
        "source_sha256": "source-checksum",
    }

    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps(manifest_data))

    restore_data = {
        "status": "passed",
        "source_identity_verified": True,
        "restore_tested_to_database": True,
        "schema_verified": True,
        "runtime_permissions_verified": True,
        "database_cleanup_verified": True,
        "temporary_resources_cleanup_verified": True,
        "source_revision": backup_source,
        "database_sha256": "database-checksum",
        "account": config.expected_account_id,
        "cluster": config.cluster_name,
        "database": "app",
    }

    restore_path = tmp_path / "restore.json"

    def save():
        manifest.write_text(json.dumps(manifest_data))
        restore_path.write_text(json.dumps(restore_data))

    save()

    monkeypatch.setattr(
        lifecycle, "_require_tools", lambda *args: None
    )
    monkeypatch.setattr(
        lifecycle, "discover_latest_backup_manifest",
        lambda cfg: manifest
    )
    monkeypatch.setattr(
        lifecycle, "database_connection",
        lambda cfg: (
            {"rds_master_secret_arn": current_secret},
            "db.example.internal",
            "app",
        )
    )
    monkeypatch.setattr(
        lifecycle, "current_source_revision", lambda: controller
    )
    monkeypatch.setattr(
        lifecycle, "_private_evidence_file",
        lambda cfg, path, description: Path(path)
    )
    monkeypatch.setattr(
        lifecycle, "environment", lambda cfg: {}
    )
    monkeypatch.setattr(
        lifecycle, "output",
        lambda *args, **kwargs: json.dumps({
            "DBInstances": [{
                "DBInstanceStatus": "available",
                "Endpoint": {"Address": "db.example.internal"},
                "MasterUserSecret": {"SecretArn": current_secret},
            }]
        })
    )
    monkeypatch.setattr(
        lifecycle, "discover_latest_restore_test",
        lambda cfg: restore_path
    )
    monkeypatch.setattr(
        lifecycle, "_verify_backup_restore_order",
        lambda *args, **kwargs: True
    )
    monkeypatch.setattr(
        lifecycle, "validate_path_boundary",
        lambda path, **kwargs: Path(path)
    )

    def checksum(path):
        return (
            "database-checksum"
            if Path(path).suffix == ".age"
            else "source-checksum"
        )

    monkeypatch.setattr(
        lifecycle, "sha256_approved_file", checksum
    )
    monkeypatch.setattr(
        lifecycle, "age_identity",
        lambda: tmp_path / "identity.txt"
    )

    def fake_call(args, **kwargs):
        if args[0] == "age":
            return b"PGDMP-mocked-offline-archive"
        if args[0] == "pg_restore":
            return b""
        raise AssertionError("Unexpected external command")

    monkeypatch.setattr(lifecycle, "call", fake_call)

    return SimpleNamespace(
        config=config,
        manifest=manifest,
        manifest_data=manifest_data,
        restore_data=restore_data,
        save=save,
        controller=controller,
        backup_source=backup_source,
    )


def test_normal_rejects_mismatched_revision(manifest_case):
    case = manifest_case

    with pytest.raises(
        LifecycleError, match="Backup identity does not match"
    ):
        lifecycle.validate_backup_manifest(case.config)


def test_normal_accepts_matching_revision(manifest_case):
    case = manifest_case
    case.manifest_data["source_revision"] = case.controller
    case.restore_data["source_revision"] = case.controller
    case.save()

    result = lifecycle.validate_backup_manifest(case.config)

    assert result["source_revision"] == case.controller
    assert result["recovery_controller_revision"] == case.controller


def test_approved_historical_dev_allows_distinct_revision(manifest_case):
    case = manifest_case

    result = lifecycle.validate_backup_manifest(
        case.config, historical_dev=True
    )

    assert result["source_revision"] == case.backup_source
    assert result["recovery_controller_revision"] == case.controller


@pytest.mark.parametrize(
    "field,value",
    [
        ("status", "failed"),
        ("source_revision", "c" * 40),
        ("database_sha256", "wrong"),
        ("source_identity_verified", False),
        ("restore_tested_to_database", False),
        ("schema_verified", False),
        ("runtime_permissions_verified", False),
        ("database_cleanup_verified", False),
        ("temporary_resources_cleanup_verified", False),
        ("account", "000000000000"),
        ("cluster", "incorrect"),
        ("database", "incorrect"),
    ],
)
def test_historical_restore_provenance_rejects_invalid_evidence(
    manifest_case, field, value
):
    case = manifest_case
    case.restore_data[field] = value
    case.save()

    with pytest.raises(
        LifecycleError, match="restore-test provenance mismatch"
    ):
        lifecycle.validate_backup_manifest(
            case.config, historical_dev=True
        )


@pytest.mark.parametrize(
    "field,value",
    [
        ("database_sha256", "incorrect"),
        ("source_sha256", "incorrect"),
    ],
)
def test_historical_rejects_archive_checksum_mismatch(
    manifest_case, field, value
):
    case = manifest_case
    case.manifest_data[field] = value
    case.save()

    # A changed database checksum must fail the earlier restore-test
    # provenance gate. The source archive checksum reaches the later
    # file-integrity gate.
    expected = (
        "restore-test provenance mismatch"
        if field == "database_sha256"
        else "checksum mismatch"
    )

    with pytest.raises(LifecycleError, match=expected):
        lifecycle.validate_backup_manifest(
            case.config, historical_dev=True
        )


def test_historical_rejects_unapproved_account(manifest_case):
    case = manifest_case
    case.config.expected_account_id = "000000000000"

    with pytest.raises(
        LifecycleError, match="approved DEV lab"
    ):
        lifecycle.validate_backup_manifest(
            case.config, historical_dev=True
        )
