"""Offline tests for Lifecycle Batch 2 platform recovery hardening."""

import json
import sys
from pathlib import Path
from unittest.mock import Mock

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts/lifecycle"))

from foundation import CheckpointStore, LabConfig, LifecycleError
from lifecycle import validate_command_argv
from recover_platform import (
    ALEMBIC_HEAD,
    GRAFANA_SECRET,
    OBSERVABILITY_CRDS,
    PLATFORM_STAGES,
    STAGE3_REVISION,
    PlatformRecovery,
    validate_source_contract,
)


def manager(tmp_path, executor=None):
    return PlatformRecovery(
        LabConfig(evidence_dir=tmp_path),
        CheckpointStore(tmp_path / "platform-checkpoint.json"),
        executor=executor or Mock(return_value=b""),
    )


def test_approved_source_contract():
    validate_source_contract()
    assert ALEMBIC_HEAD == "fe56fa70289e"
    assert STAGE3_REVISION == "540ed080c0dd52f0e6855c754f687f999887dc9f"
    assert len(OBSERVABILITY_CRDS) == 10
    assert PLATFORM_STAGES.index("observability_crds") < PLATFORM_STAGES.index(
        "observability"
    )


def test_argocd_is_an_explicitly_allowed_lifecycle_command():
    assert validate_command_argv(["argocd", "app", "sync", "platform-root"])[0]
    assert validate_command_argv(["argocd", "app", "wait", "platform-root"])[0]


def test_revalidated_stage_is_not_reapplied(tmp_path):
    recovery = manager(tmp_path)
    action = Mock()

    recovery.run_stage("argocd", Mock(return_value=True), action)

    action.assert_not_called()
    assert recovery.checkpoint.completed("argocd")
    assert (
        recovery.checkpoint.load()["stages"]["argocd"]["detail"]
        == "Revalidated actual state"
    )


def test_interrupted_stage_is_failed_and_retryable(tmp_path):
    recovery = manager(tmp_path)

    with pytest.raises(RuntimeError):
        recovery.run_stage(
            "observability_crds",
            Mock(return_value=False),
            Mock(side_effect=RuntimeError("interrupted")),
        )

    stage = recovery.checkpoint.load()["stages"]["observability_crds"]
    assert stage["status"] == "failed"
    assert stage["detail"] == "RuntimeError"

    recovery.run_stage(
        "observability_crds",
        Mock(return_value=True),
        Mock(side_effect=AssertionError("must not rerun")),
    )
    assert recovery.checkpoint.completed("observability_crds")


def test_unknown_readiness_fails_closed_before_action(tmp_path):
    recovery = manager(tmp_path)
    action = Mock()

    with pytest.raises(LifecycleError, match="access denied"):
        recovery.run_stage(
            "controllers",
            Mock(side_effect=LifecycleError("access denied")),
            action,
        )

    action.assert_not_called()
    assert (
        recovery.checkpoint.load()["stages"]["controllers"]["status"]
        == "failed"
    )



def test_appprojects_precede_root_and_manual_sync(tmp_path):
    calls = []

    def executor(args, **kwargs):
        calls.append(list(args))
        return b""

    recovery = manager(tmp_path, executor)
    recovery._sync_application = Mock()
    recovery._get_application = Mock(
        side_effect=lambda name: {
            "metadata": {"name": name},
            "spec": {"syncPolicy": {}},
        }
    )

    # Existing root-sync test assumes approved namespaces are Ready.
    # Missing/incorrect PSA labels are tested separately.
    recovery.namespaces_ready = Mock(return_value=True)
    recovery.restore_gitops_dependencies()

    assert calls[:3] == [
        ["kubectl", "apply", "-f",
         "gitops/argocd/platform-bootstrap-project.yaml"],
        ["kubectl", "apply", "-f",
         "gitops/platform/platform-project.yaml"],
        ["kubectl", "apply", "-f",
         "gitops/argocd/platform-root.yaml"],
    ]

    recovery._sync_application.assert_called_once()
    args = recovery._sync_application.call_args.args

    assert args[0] == "platform-root"
    assert "--resource" in args
    assert "--prune" not in args
    assert "--force" not in args

    assert (
        "argoproj.io:Application:observability-crds"
        in args
    )



def test_grafana_secret_uses_stdin_not_command_arguments(tmp_path):
    calls = []

    def executor(args, **kwargs):
        calls.append((list(args), kwargs.get("data")))
        if "get" in args:
            return b""
        return b"created"

    recovery = manager(tmp_path, executor)
    recovery._grafana_credentials = Mock(
        return_value=("admin", "not-in-command-arguments")
    )
    recovery.restore_grafana_secret()

    create_args, payload = calls[-1]
    assert create_args == ["kubectl", "create", "-f", "-"]
    assert "not-in-command-arguments" not in " ".join(create_args)
    secret = json.loads(payload)
    assert secret["metadata"]["name"] == GRAFANA_SECRET
    assert secret["stringData"]["admin-password"] == "not-in-command-arguments"


def test_existing_malformed_grafana_secret_is_not_overwritten(tmp_path):
    recovery = manager(tmp_path)
    recovery._grafana_secret = Mock(return_value={"type": "Opaque", "data": {}})
    recovery._grafana_credentials = Mock()

    with pytest.raises(LifecycleError, match="manual review"):
        recovery.restore_grafana_secret()

    recovery._grafana_credentials.assert_not_called()


def test_unreviewed_stage3_revision_is_not_synchronized(tmp_path):
    recovery = manager(tmp_path)
    recovery._get_application = Mock(
        return_value={"spec": {"source": {"targetRevision": "main"}}}
    )

    with pytest.raises(LifecycleError, match="unreviewed Stage 3 revision"):
        recovery.restore_stage3_application()


def test_public_cli_contract_is_preserved():
    source = (ROOT / "scripts/lifecycle/labctl.py").read_text(encoding="utf-8")
    assert "native-platform-restore" in source
    assert "platform(config)" in source


def test_root_sync_is_resource_scoped(tmp_path):
    recovery = manager(tmp_path)
    recovery._sync_application = Mock()
    recovery._get_application = Mock(
        side_effect=lambda name: {
            "metadata": {"name": name},
            "spec": {"syncPolicy": {}},
        }
    )

    # Existing root-sync test assumes approved namespaces are Ready.
    # Missing/incorrect PSA labels are tested separately.
    recovery.namespaces_ready = Mock(return_value=True)
    recovery.restore_gitops_dependencies()

    recovery._sync_application.assert_called_once()
    args = recovery._sync_application.call_args.args

    assert args[0] == "platform-root"
    assert "--resource" in args
    assert "--prune" not in args
    assert "--force" not in args
    assert "argoproj.io:Application:observability-crds" in args
    assert "argoproj.io:Application:aws-load-balancer-controller" in args


@pytest.mark.parametrize("failure", ["missing", "wrong_uid", "api_error"])
def test_argocd_identity_failure_blocks_sync(tmp_path, failure):
    recovery = manager(tmp_path)
    recovery.verify_identity = Mock(return_value={})

    live = {
        "metadata": {
            "uid": "approved-uid",
            "name": "observability",
            "namespace": "argocd",
        },
        "spec": {
            "destination": {
                "server": "https://kubernetes.default.svc",
            },
            "syncPolicy": {},
        },
    }

    recovery._get_application = Mock(return_value=live)

    if failure == "api_error":
        recovery.json = Mock(
            side_effect=LifecycleError("Argo CD API unavailable")
        )
    else:
        cli = json.loads(json.dumps(live))
        cli["metadata"]["uid"] = (
            "" if failure == "missing" else "different-uid"
        )
        recovery.json = Mock(return_value=cli)

    recovery.run = Mock()

    with pytest.raises(LifecycleError):
        recovery._sync_application("observability")

    recovery.run.assert_not_called()


def test_database_requires_independent_dml_privileges(
    tmp_path, monkeypatch
):
    import recover_platform as module

    config = LabConfig(evidence_dir=tmp_path)
    monkeypatch.setattr(module, "account_guard", lambda config: None)

    monkeypatch.setattr(
        module,
        "database_connection",
        lambda config: (
            {"rds_address": "db.internal"},
            "db.internal",
            "app",
        ),
    )

    def fake_output(args, **kwargs):
        if args[0] == "aws":
            return json.dumps({
                "DBInstances": [{
                    "DBInstanceStatus": "available",
                    "Endpoint": {"Address": "db.internal"},
                }]
            })
        return "NOT_READY"

    monkeypatch.setattr(module, "output", fake_output)
    monkeypatch.setattr(
        module, "client_pod",
        lambda *args: {"spec": {"containers": [{}]}},
    )

    submitted = []

    def fake_call(args, **kwargs):
        submitted.append((args, kwargs))
        return b""

    monkeypatch.setattr(module, "call", fake_call)

    assert module.validate_recovered_database(config) is False

    payload = next(
        kwargs["data"]
        for args, kwargs in submitted
        if args[:4] == ["kubectl", "-n", "dev", "create"]
    )

    pod = json.loads(payload)
    command = pod["spec"]["containers"][0]["command"]
    sql = command[-1]

    # PostgreSQL receives SQL with single-quoted string literals.
    # The relation identifier itself contains quoted schema/table names.
    for table in ("user", "item"):
        for privilege in ("SELECT", "INSERT", "UPDATE", "DELETE"):
            expression = (
                "has_table_privilege(current_user,"
                + chr(39)
                + chr(34) + "public" + chr(34)
                + "."
                + chr(34) + table + chr(34)
                + chr(39) + ","
                + chr(39) + privilege + chr(39) + ")"
            )
            assert expression in sql

    assert "SELECT,INSERT,UPDATE,DELETE" not in sql

    expected = "|".join(
        ("t",) * 6 + ("f",) + ("t",) * 8
    )
    one_missing = "|".join(
        ("t",) * 6 + ("f",) + ("t",) * 7 + ("f",)
    )

    assert expected in command[2]
    assert one_missing != expected


def test_batch2_missing_namespaces_reconcile(tmp_path):
    recovery = manager(tmp_path)
    recovery.namespaces_ready = Mock(side_effect=[False, True])
    recovery._namespace_contract = Mock(return_value={
        name: {} for name in ("argocd", "platform-system", "dev")
    })
    recovery._sync_application = Mock()
    recovery._get_application = Mock(
        side_effect=lambda name: {
            "metadata": {"name": name},
            "spec": {"syncPolicy": {}},
        }
    )
    recovery.restore_gitops_dependencies()
    first = recovery._sync_application.call_args_list[0].args
    assert first[0] == "platform-root"
    assert ":Namespace:dev" in first
    assert ":Namespace:platform-system" in first
    assert "--prune" not in first
    assert "aws-load-balancer-controller" not in first


def test_batch2_existing_namespaces_preserved(tmp_path):
    recovery = manager(tmp_path)
    recovery.namespaces_ready = Mock(return_value=True)
    recovery._sync_application = Mock()
    recovery._get_application = Mock(
        side_effect=lambda name: {
            "metadata": {"name": name},
            "spec": {"syncPolicy": {}},
        }
    )
    recovery.restore_gitops_dependencies()
    for call in recovery._sync_application.call_args_list:
        assert ":Namespace:dev" not in call.args


def test_batch2_namespace_psa_mismatch_fails_readiness(tmp_path):
    recovery = manager(tmp_path)
    desired = recovery._namespace_contract()
    recovery.optional_json = Mock(side_effect=lambda args: {
        "metadata": {
            "name": args[3],
            "labels": {
                **desired[args[3]]["metadata"]["labels"],
                "pod-security.kubernetes.io/enforce": "privileged",
            },
        },
    })
    assert recovery.namespaces_ready() is False


@pytest.mark.parametrize(
    "health,exists,expected",
    [
        ("Healthy", True, "healthy"),
        ("Missing", False, "absent"),
        ("Degraded", True, "degraded"),
    ],
)
def test_batch2_lbc_state_classification(
    tmp_path, health, exists, expected
):
    recovery = manager(tmp_path)
    recovery._get_application = Mock(return_value={
        "metadata": {"name": "aws-load-balancer-controller"},
        "spec": {"syncPolicy": {}},
        "status": {"health": {"status": health}},
    })
    recovery.optional_json = Mock(
        return_value={
            "metadata": {"generation": 1},
            "spec": {"replicas": 1},
            "status": {
                "observedGeneration": 1,
                "readyReplicas": 1,
                "availableReplicas": 1,
            },
        } if exists else {}
    )
    assert recovery._load_balancer_controller_state() == expected


def test_batch2_lbc_unknown_fails_closed(tmp_path):
    recovery = manager(tmp_path)
    recovery._get_application = Mock(
        side_effect=LifecycleError("API unavailable")
    )
    recovery._sync_application = Mock()
    with pytest.raises(LifecycleError, match="API unavailable"):
        recovery.restore_controllers()
    recovery._sync_application.assert_not_called()


def test_batch2_lbc_healthy_drift_not_synchronized(tmp_path):
    recovery = manager(tmp_path)
    recovery._load_balancer_controller_state = Mock(
        return_value="healthy"
    )
    recovery._sync_application = Mock()
    recovery.run = Mock()
    recovery.restore_controllers()
    assert all(
        call.args[0] != "aws-load-balancer-controller"
        for call in recovery._sync_application.call_args_list
    )


def test_batch2_lbc_degraded_not_synchronized(tmp_path):
    recovery = manager(tmp_path)
    recovery._load_balancer_controller_state = Mock(
        return_value="degraded"
    )
    recovery._sync_application = Mock()
    with pytest.raises(LifecycleError, match="Degraded"):
        recovery.restore_controllers()
    recovery._sync_application.assert_not_called()
