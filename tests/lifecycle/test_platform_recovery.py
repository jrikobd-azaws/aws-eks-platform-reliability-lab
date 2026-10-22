"""Offline tests for Lifecycle Batch 2 platform recovery hardening."""

import json
import subprocess
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
    OBSERVABILITY_WORKLOADS,
    PLATFORM_STAGES,
    STAGE3_REVISION,
    PlatformRecovery,
    sanitized_failure,
    validate_source_contract,
)


def ready_workload(kind, name, uid):
    return {
        "kind": kind.title(),
        "metadata": {"name": name, "uid": uid, "generation": 1, "namespace": "platform-system",
                     "labels": ({"app.kubernetes.io/managed-by": "prometheus-operator", "release": "observability"}
                                if kind.lower() == "statefulset" else {"app.kubernetes.io/instance": "observability"})},
        "spec": {"replicas": 1},
        "status": {
            "observedGeneration": 1,
            "readyReplicas": 1,
            "availableReplicas": 1,
        },
    }


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


def _observability_manager(tmp_path, overrides=None):
    recovery = manager(tmp_path)
    recovery._get_application = Mock(return_value={
        "spec": {"syncPolicy": {}, "sources": [{}, {}, {}]},
        "status": {"sync": {"status": "Synced"}, "health": {"status": "Healthy"}},
    })
    items = [ready_workload(kind, name, f"uid-{i}")
             for i, (kind, name, _) in enumerate(OBSERVABILITY_WORKLOADS)]
    overrides = overrides or {}
    for index, item in overrides.items():
        items[index] = item
    recovery.json = Mock(side_effect=lambda args: {
        "items": [item for item in items if item is not None and
                  item["kind"].lower() == args[args.index("get") + 1] and
                  ((item["metadata"]["labels"].get("release") == "observability")
                   if "release=observability" in args[args.index("-l") + 1]
                   else item["metadata"]["labels"].get("app.kubernetes.io/instance") == "observability")]
    })
    return recovery


def test_observability_readiness_uses_component_selectors(tmp_path):
    recovery = _observability_manager(tmp_path)
    assert recovery.observability_ready() is True
    selectors = [call.args[0][call.args[0].index("-l") + 1]
                 for call in recovery.json.call_args_list]
    assert selectors == [item[2] for item in OBSERVABILITY_WORKLOADS]
    assert "release=observability" in selectors[0]


def test_observability_missing_operator_resource_fails_closed(tmp_path):
    assert _observability_manager(tmp_path, {0: None}).observability_ready() is False


def test_observability_unready_resource_fails_closed(tmp_path):
    bad = ready_workload("statefulset", OBSERVABILITY_WORKLOADS[0][1], "uid-1")
    bad["status"]["readyReplicas"] = 0
    assert _observability_manager(tmp_path, {0: bad}).observability_ready() is False


def test_observability_wrong_owner_fails_closed(tmp_path):
    bad = ready_workload("statefulset", OBSERVABILITY_WORKLOADS[0][1], "uid-1")
    bad["metadata"]["labels"]["app.kubernetes.io/managed-by"] = "untrusted"
    with pytest.raises(LifecycleError, match="ownership mismatch"):
        _observability_manager(tmp_path, {0: bad}).observability_ready()


def test_observability_conflicting_duplicate_identity_fails_closed(tmp_path):
    recovery = _observability_manager(tmp_path)
    first = ready_workload("statefulset", OBSERVABILITY_WORKLOADS[0][1], "uid-1")
    second = ready_workload("statefulset", OBSERVABILITY_WORKLOADS[0][1], "uid-2")
    recovery.json = Mock(return_value={"items": [first, second]})
    with pytest.raises(LifecycleError, match="Duplicate observability"):
        recovery.observability_ready()


def test_external_secrets_server_side_apply_contract_preserved():
    source = (ROOT / "gitops/platform/apps/external-secrets.yaml").read_text()
    assert "ServerSideApply=true" in source


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
    recovery.argocd_server = "127.0.0.1:8443"
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


# --- Lifecycle Batch 2: Argo CD --server propagation regression tests ---


def test_argocd_server_arg_present_in_app_get(tmp_path, monkeypatch):
    """Verify --server is passed to every argocd app get CLI call."""
    recovery = manager(tmp_path)
    recovery.argocd_server = "127.0.0.1:8443"
    recovery.verify_identity = Mock(return_value={})
    monkeypatch.setattr("recover_platform.shutil.which", lambda *a, **kw: "/usr/local/bin/argocd")

    live = {
        "metadata": {
            "uid": "uid-1", "name": "platform-root", "namespace": "argocd",
        },
        "spec": {
            "destination": {"server": "https://kubernetes.default.svc"},
            "syncPolicy": {},
        },
    }
    recovery._get_application = Mock(return_value=live)
    recovery.json = Mock(return_value=live)

    recovery._verified_argocd_application("platform-root")

    call_args = recovery.json.call_args.args[0]
    assert "--server" in call_args
    assert "127.0.0.1:8443" in call_args
    assert call_args[0] == "argocd"
    assert "app" in call_args
    assert "get" in call_args


def test_argocd_server_arg_present_in_app_sync(tmp_path, monkeypatch):
    """Verify --server is passed to every argocd app sync CLI call."""
    recovery = manager(tmp_path)
    recovery.argocd_server = "127.0.0.1:8443"
    recovery.verify_identity = Mock(return_value={})
    monkeypatch.setattr("recover_platform.shutil.which", lambda *a, **kw: "/usr/local/bin/argocd")

    live = {
        "metadata": {
            "uid": "uid-1", "name": "observability", "namespace": "argocd",
        },
        "spec": {
            "destination": {"server": "https://kubernetes.default.svc"},
            "syncPolicy": {},
        },
    }
    recovery._get_application = Mock(return_value=live)
    recovery.json = Mock(return_value=live)
    recovery.run = Mock()

    recovery._sync_application("observability")

    sync_call = next(
        c for c in recovery.run.call_args_list
        if "sync" in c.args[0]
    )
    assert "--server" in sync_call.args[0]
    assert "127.0.0.1:8443" in sync_call.args[0]


def test_argocd_server_arg_present_in_app_wait(tmp_path):
    """Verify --server is passed to every argocd app wait CLI call."""
    recovery = manager(tmp_path)
    recovery.argocd_server = "127.0.0.1:8443"
    recovery._load_balancer_controller_state = Mock(
        side_effect=["absent", "healthy"]
    )
    recovery._sync_application = Mock()

    wait_calls = []

    def fake_run(args, **kwargs):
        if "argocd" in args and "wait" in args:
            wait_calls.append(list(args))
        return b""

    recovery.run = Mock(side_effect=fake_run)
    recovery.restore_controllers()

    assert len(wait_calls) >= 2
    for call in wait_calls:
        assert "--server" in call
        assert "127.0.0.1:8443" in call


def test_missing_argocd_server_blocks_sync(tmp_path):
    """Without a verified session, argocd CLI calls must fail closed."""
    recovery = manager(tmp_path)
    recovery.argocd_server = None

    with pytest.raises(LifecycleError, match="no verified session"):
        recovery._argocd_server_arg()


def test_lab_argocd_server_env_override(tmp_path, monkeypatch):
    """LAB_ARGOCD_SERVER env var provides the server when no session is active."""
    monkeypatch.setenv("LAB_ARGOCD_SERVER", "127.0.0.1:9999")
    recovery = manager(tmp_path)
    recovery.argocd_server = None
    args = recovery._argocd_server_arg()
    assert args == ["--server", "127.0.0.1:9999"]


def test_recover_propagates_verified_server_to_platform_recovery(tmp_path, monkeypatch):
    """recover() must capture the verified server from argocd_session()."""
    import recover_platform as rp
    from contextlib import contextmanager
    from bootstrap import BootstrapIdentity

    recovery = manager(tmp_path)
    recovery.bootstrap = Mock()
    recovery.bootstrap.prepare.return_value = BootstrapIdentity(
        account_id="275544662748",
        region="eu-west-1",
        cluster_arn="arn:aws:eks:eu-west-1:275544662748:cluster/eks-platform-reliability-lab",
        terraform_workspace="default",
        terraform_lineage="lineage-1",
        terraform_serial=1,
        source_revision="a" * 40,
        evidence_root=str(tmp_path.resolve()),
        kubeconfig=str(tmp_path / "kubeconfig"),
        argocd_cli="/approved/argocd",
        backend_fingerprint="b" * 64,
    )

    @contextmanager
    def fake_session():
        yield "127.0.0.1:8443"

    recovery.bootstrap.argocd_session = fake_session
    monkeypatch.setattr(rp, "validate_source_contract", lambda: None)
    monkeypatch.setattr(recovery, "verify_identity", Mock())
    monkeypatch.setattr(recovery, "_argocd_release", lambda: {
        "chart": "argo-cd-10.10.3", "revision": 1, "status": "deployed"
    })

    captured_server = []

    def fake_run_stage(name, ready, action):
        if name != "argocd":
            captured_server.append(recovery.argocd_server)

    monkeypatch.setattr(recovery, "run_stage", fake_run_stage)
    recovery.recover()

    assert all(s == "127.0.0.1:8443" for s in captured_server)
    assert len(captured_server) > 0


def test_argocd_cli_k8s_identity_match_with_server(tmp_path, monkeypatch):
    """CLI and Kubernetes must agree on Application identity when --server is supplied."""
    recovery = manager(tmp_path)
    recovery.argocd_server = "127.0.0.1:8443"
    recovery.verify_identity = Mock(return_value={})
    monkeypatch.setattr("recover_platform.shutil.which", lambda *a, **kw: "/usr/local/bin/argocd")

    live = {
        "metadata": {
            "uid": "uid-approved", "name": "stage3-dev", "namespace": "argocd",
        },
        "spec": {
            "destination": {"server": "https://kubernetes.default.svc"},
            "syncPolicy": {},
        },
    }
    recovery._get_application = Mock(return_value=live)
    recovery.json = Mock(return_value=live)

    result = recovery._verified_argocd_application("stage3-dev")
    assert result == live
    cli_call = recovery.json.call_args.args[0]
    assert "--server" in cli_call


def test_argocd_cli_identity_mismatch_with_server_blocks(tmp_path, monkeypatch):
    """Identity mismatch must block even when --server is correctly supplied."""
    recovery = manager(tmp_path)
    recovery.argocd_server = "127.0.0.1:8443"
    recovery.verify_identity = Mock(return_value={})
    monkeypatch.setattr("recover_platform.shutil.which", lambda *a, **kw: "/usr/local/bin/argocd")

    live = {
        "metadata": {
            "uid": "uid-approved", "name": "platform-root", "namespace": "argocd",
        },
        "spec": {
            "destination": {"server": "https://kubernetes.default.svc"},
            "syncPolicy": {},
        },
    }
    cli_response = json.loads(json.dumps(live))
    cli_response["metadata"]["uid"] = "uid-different"
    recovery._get_application = Mock(return_value=live)
    recovery.json = Mock(return_value=cli_response)
    recovery.run = Mock()

    with pytest.raises(LifecycleError, match="identity mismatch"):
        recovery._sync_application("platform-root")

    recovery.run.assert_not_called()


def test_manual_sync_enforced_with_server(tmp_path, monkeypatch):
    """Automatic sync policy must block even when --server is supplied."""
    recovery = manager(tmp_path)
    recovery.argocd_server = "127.0.0.1:8443"
    recovery.verify_identity = Mock(return_value={})
    monkeypatch.setattr("recover_platform.shutil.which", lambda *a, **kw: "/usr/local/bin/argocd")

    auto_sync = {
        "metadata": {
            "uid": "uid-1", "name": "observability", "namespace": "argocd",
        },
        "spec": {
            "destination": {"server": "https://kubernetes.default.svc"},
            "syncPolicy": {"automated": {"prune": True}},
        },
    }
    recovery._get_application = Mock(return_value=auto_sync)
    recovery.json = Mock(return_value=auto_sync)
    recovery.run = Mock()

    with pytest.raises(LifecycleError, match="manual synchronization"):
        recovery._sync_application("observability")

    recovery.run.assert_not_called()


def test_no_prune_no_force_with_server(tmp_path, monkeypatch):
    """Unsafe sync options must be blocked even when --server is supplied."""
    recovery = manager(tmp_path)
    recovery.argocd_server = "127.0.0.1:8443"
    recovery.verify_identity = Mock(return_value={})
    monkeypatch.setattr("recover_platform.shutil.which", lambda *a, **kw: "/usr/local/bin/argocd")

    live = {
        "metadata": {
            "uid": "uid-1", "name": "observability", "namespace": "argocd",
        },
        "spec": {
            "destination": {"server": "https://kubernetes.default.svc"},
            "syncPolicy": {},
        },
    }
    recovery._get_application = Mock(return_value=live)
    recovery.json = Mock(return_value=live)
    recovery.run = Mock()

    with pytest.raises(LifecycleError, match="Unsafe Argo CD"):
        recovery._sync_application("observability", "--prune")

    with pytest.raises(LifecycleError, match="Unsafe Argo CD"):
        recovery._sync_application("observability", "--force")


# --- Lifecycle Batch 2: exit-code-20 classification regression tests ---


def test_exit_code_20_classified_as_argocd_server_selection():
    """Argo CD CLI exit code 20 must be classified as ARGOCD_SERVER_SELECTION."""
    import lifecycle as n

    error = subprocess.CalledProcessError(
        20, ["argocd", "app", "get", "platform-root", "-o", "json"]
    )
    assert n._classify_subprocess_error(error) == "ARGOCD_SERVER_SELECTION"


def test_exit_code_20_non_argocd_is_unclassified():
    """Exit code 20 from non-argocd binaries remains UNCLASSIFIED."""
    import lifecycle as n

    error = subprocess.CalledProcessError(
        20, ["kubectl", "get", "pods"]
    )
    assert n._classify_subprocess_error(error) == "UNCLASSIFIED"


def test_call_error_message_includes_category_for_argocd_exit_20(monkeypatch):
    """LifecycleError from argocd exit 20 must include the ARGOCD_SERVER_SELECTION category."""
    import lifecycle as n

    error = subprocess.CalledProcessError(
        20, ["argocd", "app", "get", "platform-root", "-o", "json"]
    )
    monkeypatch.setattr(subprocess, "run", Mock(side_effect=error))
    with pytest.raises(LifecycleError) as exc:
        n.call(["argocd", "app", "get", "platform-root", "-o", "json"])
    assert "ARGOCD_SERVER_SELECTION" in str(exc.value)
    assert "exit=20" in str(exc.value)


# --- Lifecycle Batch 2: sanitized_failure structured reporting tests ---


def test_sanitized_failure_classifies_argocd_server_selection():
    """sanitized_failure must classify exit-code-20 errors correctly."""
    exc = LifecycleError(
        "Command failed: argocd (exit=20; category=ARGOCD_SERVER_SELECTION; "
        "private stderr suppressed)"
    )
    message = sanitized_failure(exc)
    assert "ARGOCD_SERVER_SELECTION" in message
    assert "Corrective action" in message
    assert "Retry:" in message
    assert "session" in message.lower()


def test_sanitized_failure_classifies_identity_mismatch():
    """sanitized_failure must classify identity mismatch as requiring approval."""
    exc = LifecycleError("Argo CD CLI Application identity mismatch")
    message = sanitized_failure(exc)
    assert "IDENTITY_MISMATCH" in message
    assert "requires approval" in message


def test_sanitized_failure_classifies_non_empty_database():
    """sanitized_failure must classify non-empty database as requiring approval."""
    exc = LifecycleError("Target database is not empty")
    message = sanitized_failure(exc)
    assert "NON_EMPTY_TARGET" in message
    assert "requires approval" in message


def test_sanitized_failure_classifies_unsafe_sync_option():
    """sanitized_failure must classify prune/force as safe-after-correction."""
    exc = LifecycleError("Unsafe Argo CD synchronization option")
    message = sanitized_failure(exc)
    assert "UNSAFE_SYNC_OPTION" in message
    assert "safe after correction" in message


def test_sanitized_failure_still_redacts_secrets():
    """Structured reporting must still redact sensitive values."""
    exc = LifecycleError(
        "login failed password=secret123 token:abc456"
    )
    message = sanitized_failure(exc)
    assert "secret123" not in message
    assert "abc456" not in message
    assert "[REDACTED]" in message


def test_sanitized_failure_for_non_lifecycle_error():
    """Non-LifecycleError must be reported as BLOCKED with investigation required."""
    exc = RuntimeError("unexpected failure")
    message = sanitized_failure(exc)
    assert message.startswith("BLOCKED: RuntimeError")
    assert "requires investigation" in message
