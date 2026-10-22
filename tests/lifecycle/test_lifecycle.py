"""Tests for selective Terraform lifecycle policy."""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts/lifecycle"))

from foundation import LifecycleError
from lifecycle import (
    classify_resources,
    is_data_source,
    is_protected,
    validate_destroy_plan,
)


def change(address, actions, mode="managed"):
    return {
        "address": address,
        "mode": mode,
        "change": {"actions": actions},
    }


def test_nested_data_sources():
    assert is_data_source(
        "module.eks.data.aws_eks_addon_version.coredns"
    )


def test_protected_ecr():
    assert is_protected(
        'aws_ecr_repository.stage3["backend"]'
    )


def test_protected_nested_secret():
    assert is_protected(
        "module.example.aws_secretsmanager_secret.credential"
    )


def test_classification():
    targets = classify_resources([
        'aws_ecr_repository.stage3["backend"]',
        "module.eks.data.aws_eks_addon_version.coredns",
        "module.eks.aws_eks_cluster.this",
        "module.rds.aws_db_instance.this",
    ])

    assert len(targets) == 2


def test_unknown_resource_rejected():
    with pytest.raises(LifecycleError):
        classify_resources(["aws_instance.unclassified"])


def test_plan_accepts_approved_deletions():
    address = "module.eks.aws_eks_cluster.this"

    plan = {
        "resource_changes": [
            change(address, ["delete"]),
            change(
                "module.eks.data.tls_certificate.cluster",
                ["read"],
                "data",
            ),
        ]
    }

    result = validate_destroy_plan(plan, [address])

    assert result["delete_count"] == 1
    assert result["data_source_reads"] == 1


def test_plan_rejects_ecr_delete():
    address = 'aws_ecr_repository.stage3["backend"]'

    plan = {
        "resource_changes": [
            change(address, ["delete"])
        ]
    }

    with pytest.raises(LifecycleError):
        validate_destroy_plan(plan, [address])


def test_plan_rejects_unexpected_update():
    address = "module.eks.aws_eks_cluster.this"

    plan = {
        "resource_changes": [
            change(address, ["update"])
        ]
    }

    with pytest.raises(LifecycleError):
        validate_destroy_plan(plan, [address])


def test_plan_rejects_extra_delete():
    plan = {
        "resource_changes": [
            change("module.eks.aws_eks_cluster.this", ["delete"]),
            change("module.rds.aws_db_instance.this", ["delete"]),
        ]
    }

    with pytest.raises(LifecycleError):
        validate_destroy_plan(
            plan,
            ["module.eks.aws_eks_cluster.this"],
        )


def test_plan_rejects_missing_target():
    plan = {"resource_changes": []}

    with pytest.raises(LifecycleError):
        validate_destroy_plan(
            plan,
            ["module.eks.aws_eks_cluster.this"],
        )


@pytest.mark.parametrize("changed", [
    {"source_revision": "b" * 40},
    {"database_sha256": "wrong"},
    {"account": "000000000000"},
    {"cluster": "wrong"},
    {"database": "other"},
    {"schema_verified": False},
    {"temporary_resources_cleanup_verified": False},
])
def test_historical_backup_provenance_rejects_mismatch(monkeypatch, changed):
    from types import SimpleNamespace
    from lifecycle import verify_backup_restore_provenance
    config = SimpleNamespace(expected_account_id="275544662748", cluster_name="lab")
    backup = {
        "manifest_data": {"source_revision": "a" * 40},
        "database_sha256": "checksum", "database": "app",
    }
    restore = {
        "status": "passed", "restore_tested_to_database": True,
        "schema_verified": True, "runtime_permissions_verified": True,
        "database_cleanup_verified": True,
        "temporary_resources_cleanup_verified": True,
        "source_identity_verified": True,
        "source_revision": "a" * 40, "database_sha256": "checksum",
        "account": "275544662748", "cluster": "lab", "database": "app",
    }
    restore.update(changed)
    monkeypatch.setattr("lifecycle._verify_backup_restore_order", lambda *a, **kw: True)
    with pytest.raises(LifecycleError, match="provenance mismatch"):
        verify_backup_restore_provenance(config, backup, restore)


def test_historical_backup_provenance_accepts_distinct_controller_revision(monkeypatch):
    from types import SimpleNamespace
    from lifecycle import verify_backup_restore_provenance
    config = SimpleNamespace(expected_account_id="275544662748", cluster_name="lab")
    backup = {
        "manifest_data": {"source_revision": "a" * 40},
        "database_sha256": "checksum", "database": "app",
        "recovery_controller_revision": "b" * 40,
    }
    restore = {
        "status": "passed", "restore_tested_to_database": True,
        "schema_verified": True, "runtime_permissions_verified": True,
        "database_cleanup_verified": True,
        "temporary_resources_cleanup_verified": True,
        "source_identity_verified": True,
        "source_revision": "a" * 40, "database_sha256": "checksum",
        "account": "275544662748", "cluster": "lab", "database": "app",
    }
    monkeypatch.setattr("lifecycle._verify_backup_restore_order", lambda *a, **kw: True)
    assert verify_backup_restore_provenance(config, backup, restore)
