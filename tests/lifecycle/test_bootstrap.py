"""Behavioral regressions for the permanent recovery bootstrap."""

import json
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import Mock

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts/lifecycle"))

from bootstrap import ACTION_POLICY, ActionClass, RecoveryBootstrap
from foundation import CheckpointStore, CommandRunner, LabConfig, LifecycleError


def config(tmp_path):
    return LabConfig(terraform_dir=tmp_path / "terraform", evidence_dir=tmp_path / "evidence")


def identity(serial=1, source="a" * 40, evidence="/private/evidence"):
    return {
        "account_id": "275544662748",
        "region": "eu-west-1",
        "cluster_arn": "arn:aws:eks:eu-west-1:275544662748:cluster/eks-platform-reliability-lab",
        "terraform_workspace": "default",
        "terraform_lineage": "lineage-1",
        "terraform_serial": serial,
        "source_revision": source,
        "evidence_root": evidence,
    }


def test_action_classes_are_explicit():
    assert {item.value for item in ActionClass} == {
        "AUTO_REPAIR", "APPROVAL_REQUIRED", "FAIL_CLOSED"
    }
    assert ACTION_POLICY["terraform_init"] is ActionClass.AUTO_REPAIR
    assert ACTION_POLICY["gitops_sync"] is ActionClass.APPROVAL_REQUIRED
    assert ACTION_POLICY["ambiguous_identity"] is ActionClass.FAIL_CLOSED


def test_checkpoint_reused_across_worktree_symlink(tmp_path):
    authoritative = tmp_path / "authoritative"
    authoritative.mkdir()
    alias = tmp_path / "other-worktree-evidence"
    alias.symlink_to(authoritative, target_is_directory=True)
    first = CheckpointStore(authoritative / "checkpoint.json")
    second = CheckpointStore(alias / "checkpoint.json")
    binding = identity(evidence=str(authoritative.resolve()))
    assert first.reconcile_identity(binding) is False
    first.update("platform", "passed")
    assert second.reconcile_identity(binding) is True
    assert second.completed("platform")


def test_state_serial_change_preserves_checkpoint(tmp_path):
    store = CheckpointStore(tmp_path / "checkpoint.json")
    store.reconcile_identity(identity(serial=1))
    store.update("platform", "passed")
    assert store.reconcile_identity(identity(serial=2)) is True
    assert store.completed("platform")


def test_legacy_unbound_checkpoint_is_invalidated_on_binding(tmp_path):
    store = CheckpointStore(tmp_path / "checkpoint.json")
    store.update("platform", "passed")
    assert store.reconcile_identity(identity()) is False
    assert store.load()["stages"]["platform"]["status"] == "pending"


def test_contradictory_checkpoint_identity_fails_closed(tmp_path):
    path = tmp_path / "checkpoint.json"
    path.write_text(json.dumps({"schema_version": 1, "stages": {}, "identity": "bad"}))
    with pytest.raises(LifecycleError, match="Invalid checkpoint identity"):
        CheckpointStore(path).reconcile_identity(identity())


def test_tampered_checkpoint_fingerprint_fails_closed(tmp_path):
    store = CheckpointStore(tmp_path / "checkpoint.json")
    store.reconcile_identity(identity())
    data = store.load()
    data["identity"]["values"]["cluster_arn"] = "different"
    store.path.write_text(json.dumps(data))
    with pytest.raises(LifecycleError, match="fingerprint mismatch"):
        store.reconcile_identity(identity())


def test_missing_backend_inputs_block_initialization(tmp_path, monkeypatch):
    boot = RecoveryBootstrap(config(tmp_path), Mock())
    monkeypatch.setattr(boot, "_tool", lambda name: Path("/usr/bin") / name)
    monkeypatch.delenv("LAB_TERRAFORM_BACKEND_CONFIG", raising=False)
    def unavailable_backend():
        raise LifecycleError(
            "Approved Terraform backend identity is unavailable or ambiguous"
        )
    monkeypatch.setattr(
        boot, "_approved_backend_config", unavailable_backend
    )
    with pytest.raises(LifecycleError, match="backend identity is unavailable or ambiguous"):
        boot._terraform_backend(auto_repair=True)


def test_backend_region_mismatch_fails_closed(tmp_path, monkeypatch):
    cfg = config(tmp_path)
    metadata = cfg.terraform_dir / ".terraform" / "terraform.tfstate"
    metadata.parent.mkdir(parents=True)
    metadata.write_text(json.dumps({
        "backend": {"type": "s3", "config": {
            "bucket": "approved", "key": "lab/state", "region": "us-east-1"
        }}
    }))
    boot = RecoveryBootstrap(cfg, Mock())
    monkeypatch.setattr(boot, "_tool", lambda name: Path("/usr/bin") / name)
    with pytest.raises(LifecycleError, match="backend region mismatch"):
        boot._terraform_backend(auto_repair=False)


def test_home_and_aws_profile_are_preserved(monkeypatch):
    monkeypatch.setenv("HOME", "/home/operator")
    monkeypatch.setenv("AWS_PROFILE", "unrelated")
    env = LabConfig(aws_profile="approved-profile").environment()
    assert env["HOME"] == "/home/operator"
    assert env["AWS_PROFILE"] == "approved-profile"


def test_stale_kubeconfig_rebuilt_atomically(tmp_path, monkeypatch):
    cfg = config(tmp_path)
    kubeconfig = tmp_path / "isolated" / "config"
    kubeconfig.parent.mkdir()
    kubeconfig.write_text("stale")
    monkeypatch.setenv("KUBECONFIG", str(kubeconfig))

    runner = Mock()
    def create_config(args, **kwargs):
        if "update-kubeconfig" in args:
            Path(args[-1]).write_text("verified")
        return subprocess.CompletedProcess(args, 0, "", "")
    runner.run.side_effect = create_config
    boot = RecoveryBootstrap(cfg, runner)
    monkeypatch.setattr(boot, "_tool", lambda name: Path("/usr/bin") / name)
    monkeypatch.setattr(
        boot, "_kubeconfig_matches", lambda path, cluster: path.read_text() == "verified"
    )
    result = boot._ensure_kubeconfig({}, auto_repair=True)
    assert result == kubeconfig.resolve()
    assert result.read_text() == "verified"
    assert list(kubeconfig.parent.glob("config.stale-*"))


def test_wrong_eks_context_does_not_authorize_repair(tmp_path, monkeypatch):
    cfg = config(tmp_path)
    kubeconfig = tmp_path / "isolated-config"
    kubeconfig.write_text("wrong")
    monkeypatch.setenv("KUBECONFIG", str(kubeconfig))
    boot = RecoveryBootstrap(cfg, Mock())
    monkeypatch.setattr(boot, "_tool", lambda name: Path("/usr/bin") / name)
    monkeypatch.setattr(boot, "_kubeconfig_matches", lambda path, cluster: False)
    with pytest.raises(LifecycleError, match="missing or stale"):
        boot._ensure_kubeconfig({}, auto_repair=False)
    boot.runner.run.assert_not_called()


def test_missing_argocd_cli_fails_closed(tmp_path, monkeypatch):
    boot = RecoveryBootstrap(config(tmp_path), Mock())
    monkeypatch.delenv("LAB_ARGOCD_CLI", raising=False)
    monkeypatch.setattr("bootstrap.shutil.which", lambda *args, **kwargs: None)
    monkeypatch.setattr(boot, "_evidence_candidates", lambda: [])
    with pytest.raises(LifecycleError, match="Approved Argo CD CLI unavailable"):
        boot._argocd_tool()


def test_expired_argocd_auth_does_not_start_second_tunnel(tmp_path, monkeypatch):
    boot = RecoveryBootstrap(config(tmp_path), Mock())
    boot._argocd_cli = Path("/approved/argocd")
    monkeypatch.setenv("LAB_ARGOCD_SERVER", "127.0.0.1:8443")
    original_path = os.environ.get("PATH")
    monkeypatch.setattr(boot, "_listening", lambda port: True)
    boot.runner.run.side_effect = LifecycleError("authentication expired")
    with pytest.raises(LifecycleError, match="authentication expired"):
        with boot.argocd_session():
            pass
    assert boot._owned_tunnel is None
    assert os.environ.get("PATH") == original_path


def test_ambiguous_argocd_context_fails_closed(tmp_path, monkeypatch):
    monkeypatch.delenv("LAB_ARGOCD_SERVER", raising=False)
    boot = RecoveryBootstrap(config(tmp_path), Mock())
    boot.runner.run.return_value = subprocess.CompletedProcess(
        [], 0, "localhost:8080\nlocalhost:8443\n", ""
    )
    with pytest.raises(LifecycleError, match="ambiguous"):
        boot._argocd_server(Path("/approved/argocd"))


def test_current_argocd_context_is_selected(tmp_path, monkeypatch):
    monkeypatch.delenv("LAB_ARGOCD_SERVER", raising=False)
    boot = RecoveryBootstrap(config(tmp_path), Mock())
    boot.runner.run.return_value = subprocess.CompletedProcess(
        [], 0,
        "CURRENT NAME SERVER\n  old old.example:443\n* local localhost:8443\n",
        "",
    )
    assert boot._argocd_server(Path("/approved/argocd")) == "localhost:8443"


def test_failed_port_forward_is_cleaned_up(tmp_path, monkeypatch):
    process = Mock()
    process.poll.side_effect = [None, None, None]
    process.wait.return_value = 0
    boot = RecoveryBootstrap(
        config(tmp_path), Mock(), popen_factory=Mock(return_value=process), sleep=lambda _: None
    )
    boot._argocd_cli = Path("/approved/argocd")
    monkeypatch.setenv("LAB_ARGOCD_SERVER", "localhost:8443")
    monkeypatch.setattr(boot, "_listening", lambda port: False)
    monkeypatch.setattr(boot, "_json", lambda *a, **k: {"spec": {"ports": [{"name": "https", "port": 443}]}})
    process.poll.side_effect = [None] * 25
    with pytest.raises(LifecycleError, match="did not become ready"):
        with boot.argocd_session():
            pass
    process.terminate.assert_called_once()
    assert boot._owned_tunnel is None


def test_bootstrap_source_contains_no_terraform_apply_or_destroy():
    source = (ROOT / "scripts/lifecycle/bootstrap.py").read_text(encoding="utf-8")
    assert '"apply"' not in source
    assert '"destroy"' not in source
    assert "argocd app sync" not in source
    assert "database-restore" not in source


def test_source_revision_change_does_not_invalidate_verified_identity(tmp_path):
    store = CheckpointStore(tmp_path / "checkpoint.json")
    store.reconcile_identity(identity())
    store.update("platform", "passed")
    assert store.reconcile_identity(identity(source="b" * 40)) is True
    assert store.completed("platform")


def test_cluster_change_invalidates_checkpoint(tmp_path):
    store = CheckpointStore(tmp_path / "checkpoint.json")
    first = identity()
    store.reconcile_identity(first)
    store.update("platform", "passed")
    changed = dict(first, cluster_arn=first["cluster_arn"] + "-other")
    assert store.reconcile_identity(changed) is False
    assert not store.completed("platform")


def test_backend_auto_init_uses_hcl_and_readonly_lockfile(tmp_path, monkeypatch):
    cfg = config(tmp_path)
    cfg.terraform_dir.mkdir()
    (cfg.terraform_dir / "backend.tf").write_text('terraform { backend "s3" { key = "lab/state" region = "eu-west-1" } }')
    runner = Mock()
    metadata = cfg.terraform_dir / ".terraform/terraform.tfstate"

    def run(args, **kwargs):
        assert "init" in args
        assert "-lockfile=readonly" in args
        path = Path(next(str(x).split("=", 1)[1] for x in args if str(x).startswith("-backend-config=")))
        content = path.read_text()
        assert 'bucket = "approved-bucket"' in content
        assert 'key = "lab/state"' in content
        assert not content.lstrip().startswith("{")
        metadata.parent.mkdir()
        metadata.write_text(json.dumps({"backend": {"type": "s3", "config": {
            "bucket": "approved-bucket", "key": "lab/state", "region": "eu-west-1"
        }}}))
        return subprocess.CompletedProcess(args, 0, "", "")

    runner.run.side_effect = run
    boot = RecoveryBootstrap(cfg, runner)
    monkeypatch.setattr(boot, "_tool", lambda name: Path("/usr/bin") / name)
    monkeypatch.setattr(boot, "_approved_backend_config", lambda: {
        "bucket": "approved-bucket", "key": "lab/state", "region": "eu-west-1"
    })
    assert boot._terraform_backend(auto_repair=True)["bucket"] == "approved-bucket"


def test_missing_kubeconfig_discovers_verified_private_copy(tmp_path, monkeypatch):
    cfg = config(tmp_path)
    evidence = cfg.evidence_dir
    existing = evidence / "previous-run/kubeconfig"
    existing.parent.mkdir(parents=True)
    existing.write_text("private-kubeconfig")
    existing.chmod(0o600)
    monkeypatch.delenv("KUBECONFIG", raising=False)
    boot = RecoveryBootstrap(cfg, Mock())
    monkeypatch.setattr(boot, "_evidence_candidates", lambda: [evidence])
    monkeypatch.setattr(boot, "_kubeconfig_matches", lambda path, cluster: path == existing)
    assert boot._isolated_kubeconfig({"endpoint": "verified"}) == existing


def test_argocd_service_port_is_discovered(tmp_path, monkeypatch):
    cfg = config(tmp_path)
    process = Mock()
    process.poll.return_value = None
    process.wait.return_value = 0
    popen = Mock(return_value=process)
    boot = RecoveryBootstrap(cfg, Mock(), popen_factory=popen)
    boot._argocd_cli = Path("/approved/argocd")
    monkeypatch.setenv("LAB_ARGOCD_SERVER", "127.0.0.1:8443")
    checks = iter([False, True])
    monkeypatch.setattr(boot, "_listening", lambda port: next(checks))
    monkeypatch.setattr(boot, "_json", lambda *a, **k: {
        "spec": {"ports": [{"name": "https", "port": 9443}]}
    })
    boot._verify_argocd_auth = Mock()
    boot._argocd_login = Mock()
    with boot.argocd_session():
        pass
    assert "8443:9443" in popen.call_args.args[0]
    process.terminate.assert_called_once()


def test_native_kubeconfig_accepts_verified_eks_identity(tmp_path):
    import base64
    import yaml

    ca = b"synthetic-test-ca-certificate"
    encoded = base64.b64encode(ca).decode()

    cluster = {
        "endpoint": "https://approved.eks.example",
        "certificateAuthority": {"data": encoded},
    }

    path = tmp_path / "kubeconfig"
    path.write_text(yaml.safe_dump({
        "apiVersion": "v1",
        "kind": "Config",
        "current-context": "eks-reliability-lab",
        "contexts": [{
            "name": "eks-reliability-lab",
            "context": {
                "cluster": "approved-cluster",
                "user": "approved-user",
            },
        }],
        "clusters": [{
            "name": "approved-cluster",
            "cluster": {
                "server": cluster["endpoint"],
                "certificate-authority-data": encoded,
            },
        }],
        "users": [{"name": "approved-user", "user": {
            "exec": {
                "apiVersion": "client.authentication.k8s.io/v1beta1",
                "command": "aws",
                "args": [
                    "eks", "get-token",
                    "--region", "eu-west-1",
                    "--cluster-name", "eks-platform-reliability-lab",
                    "--output", "json",
                ],
                "env": [{"name": "AWS_PROFILE", "value": "eks-lab-terraform"}],
            }
        }}],
    }))
    path.chmod(0o600)

    boot = RecoveryBootstrap(config(tmp_path), Mock())
    assert boot._kubeconfig_matches(path, cluster)

    wrong_endpoint = dict(cluster, endpoint="https://wrong.example")
    assert not boot._kubeconfig_matches(path, wrong_endpoint)

    wrong_ca = dict(
        cluster,
        certificateAuthority={
            "data": base64.b64encode(b"different-ca").decode()
        },
    )
    assert not boot._kubeconfig_matches(path, wrong_ca)

    path.chmod(0o644)
    assert not boot._kubeconfig_matches(path, cluster)


def test_native_kubeconfig_rejects_ambiguous_context(tmp_path):
    import base64
    import yaml

    encoded = base64.b64encode(b"synthetic-ca").decode()
    cluster = {
        "endpoint": "https://approved.eks.example",
        "certificateAuthority": {"data": encoded},
    }

    path = tmp_path / "kubeconfig"
    path.write_text(yaml.safe_dump({
        "current-context": "approved",
        "contexts": [
            {"name": "approved", "context": {
                "cluster": "one", "user": "operator"}},
            {"name": "approved", "context": {
                "cluster": "two", "user": "operator"}},
        ],
        "clusters": [{
            "name": "one",
            "cluster": {
                "server": cluster["endpoint"],
                "certificate-authority-data": encoded,
            },
        }],
    }))
    path.chmod(0o600)

    boot = RecoveryBootstrap(config(tmp_path), Mock())
    assert not boot._kubeconfig_matches(path, cluster)


def test_native_kubeconfig_rejects_wrong_eks_auth_cluster(tmp_path):
    import base64
    import yaml

    ca = base64.b64encode(b"synthetic-ca").decode()

    path = tmp_path / "kubeconfig"
    path.write_text(yaml.safe_dump({
        "current-context": "approved",
        "contexts": [{
            "name": "approved",
            "context": {"cluster": "approved", "user": "operator"},
        }],
        "clusters": [{
            "name": "approved",
            "cluster": {
                "server": "https://approved.eks.example",
                "certificate-authority-data": ca,
            },
        }],
        "users": [{
            "name": "operator",
            "user": {
                "exec": {
                    "command": "aws",
                    "args": [
                        "eks", "get-token",
                        "--cluster-name", "wrong-cluster",
                    ],
                },
            },
        }],
    }))
    path.chmod(0o600)

    cluster = {
        "endpoint": "https://approved.eks.example",
        "certificateAuthority": {"data": ca},
    }

    boot = RecoveryBootstrap(config(tmp_path), Mock())

    assert not boot._kubeconfig_matches(path, cluster)


@pytest.mark.parametrize("bad_args", [
    ["eks", "get-token", "--region", "us-east-1",
     "--cluster-name", "eks-platform-reliability-lab"],
    ["eks", "get-token", "--region", "eu-west-1",
     "--cluster-name", "wrong-cluster"],
    ["eks", "get-token", "--region", "eu-west-1",
     "--cluster-name", "eks-platform-reliability-lab",
     "--cluster-name", "eks-platform-reliability-lab"],
    ["eks", "get-token", "--region", "eu-west-1",
     "--cluster-name", "eks-platform-reliability-lab",
     "--profile", "unapproved"],
])
def test_native_kubeconfig_rejects_unsafe_exec_args(tmp_path, bad_args):
    import base64
    import yaml

    ca = base64.b64encode(b"synthetic-ca").decode()
    path = tmp_path / "kubeconfig"

    path.write_text(yaml.safe_dump({
        "current-context": "approved",
        "contexts": [{
            "name": "approved",
            "context": {
                "cluster": "approved",
                "user": "operator",
            },
        }],
        "clusters": [{
            "name": "approved",
            "cluster": {
                "server": "https://approved.eks.example",
                "certificate-authority-data": ca,
            },
        }],
        "users": [{
            "name": "operator",
            "user": {
                "exec": {
                    "command": "aws",
                    "args": bad_args,
                },
            },
        }],
    }))
    path.chmod(0o600)

    cluster = {
        "endpoint": "https://approved.eks.example",
        "certificateAuthority": {"data": ca},
    }

    boot = RecoveryBootstrap(config(tmp_path), Mock())
    assert not boot._kubeconfig_matches(path, cluster)



# --- Lifecycle Batch 2: verified_server propagation regression tests ---


def test_verified_server_is_none_before_session(tmp_path):
    """verified_server must be None until argocd_session() establishes it."""
    boot = RecoveryBootstrap(config(tmp_path), Mock())
    assert boot.verified_server is None


def test_verified_server_set_after_reused_session(tmp_path, monkeypatch):
    """verified_server must be set when reusing an existing authenticated session."""
    boot = RecoveryBootstrap(config(tmp_path), Mock())
    boot._argocd_cli = Path("/approved/argocd")
    monkeypatch.setenv("LAB_ARGOCD_SERVER", "127.0.0.1:8443")
    monkeypatch.setattr(boot, "_listening", lambda port: True)
    boot.runner.run.side_effect = lambda *a, **kw: subprocess.CompletedProcess(
        [], 0, "", ""
    )
    with boot.argocd_session() as server:
        assert server == "127.0.0.1:8443"
        assert boot.verified_server == "127.0.0.1:8443"


def test_verified_server_set_after_new_tunnel(tmp_path, monkeypatch):
    """verified_server must be set after establishing a new port-forward tunnel."""
    process = Mock()
    process.poll.return_value = None
    process.wait.return_value = 0
    popen = Mock(return_value=process)
    boot = RecoveryBootstrap(
        config(tmp_path), Mock(), popen_factory=popen
    )
    boot._argocd_cli = Path("/approved/argocd")
    monkeypatch.setenv("LAB_ARGOCD_SERVER", "127.0.0.1:8443")
    checks = iter([False, True])
    monkeypatch.setattr(boot, "_listening", lambda port: next(checks))
    monkeypatch.setattr(boot, "_json", lambda *a, **k: {
        "spec": {"ports": [{"name": "https", "port": 9443}]}
    })
    boot._verify_argocd_auth = Mock()
    boot._argocd_login = Mock()
    with boot.argocd_session() as server:
        assert server == "127.0.0.1:8443"
        assert boot.verified_server == "127.0.0.1:8443"



# --- Lifecycle Batch 2: Argo CD Application API authentication regression tests ---


def test_expired_credentials_refresh_via_login(tmp_path, monkeypatch):
    """When _verify_argocd_auth fails on a listening port, argocd_login must be called."""
    from foundation import LifecycleError

    boot = RecoveryBootstrap(config(tmp_path), Mock())
    boot._argocd_cli = Path("/approved/argocd")
    monkeypatch.setenv("LAB_ARGOCD_SERVER", "127.0.0.1:8443")
    monkeypatch.setattr(boot, "_listening", lambda port: True)

    call_sequence = []

    def fail_then_pass(cli, server):
        call_sequence.append("verify")
        if len(call_sequence) == 1:
            raise LifecycleError("UNAUTHENTICATED")

    boot._verify_argocd_auth = Mock(side_effect=fail_then_pass)
    boot._argocd_login = Mock()

    with boot.argocd_session() as server:
        assert server == "127.0.0.1:8443"

    assert call_sequence == ["verify", "verify"]
    boot._argocd_login.assert_called_once()
    assert boot.verified_server == "127.0.0.1:8443"


def test_new_tunnel_reuses_valid_context(tmp_path, monkeypatch):
    """A new tunnel on a different port must always perform argocd login."""
    process = Mock()
    process.poll.return_value = None
    process.wait.return_value = 0
    popen = Mock(return_value=process)
    boot = RecoveryBootstrap(
        config(tmp_path), Mock(), popen_factory=popen
    )
    boot._argocd_cli = Path("/approved/argocd")
    monkeypatch.setenv("LAB_ARGOCD_SERVER", "127.0.0.1:8443")
    checks = iter([False, True])
    monkeypatch.setattr(boot, "_listening", lambda port: next(checks))
    monkeypatch.setattr(boot, "_json", lambda *a, **k: {
        "spec": {"ports": [{"name": "https", "port": 9443}]}
    })
    boot._verify_argocd_auth = Mock()
    boot._argocd_login = Mock()

    with boot.argocd_session() as server:
        assert server == "127.0.0.1:8443"

    boot._argocd_login.assert_not_called()
    boot._verify_argocd_auth.assert_called_once()


def test_login_failure_blocks_session(tmp_path, monkeypatch):
    """If login fails, the session must not yield."""
    from foundation import LifecycleError

    boot = RecoveryBootstrap(config(tmp_path), Mock())
    boot._argocd_cli = Path("/approved/argocd")
    monkeypatch.setenv("LAB_ARGOCD_SERVER", "127.0.0.1:8443")
    monkeypatch.setattr(boot, "_listening", lambda port: True)
    boot._verify_argocd_auth = Mock(side_effect=LifecycleError("UNAUTHENTICATED"))
    boot._argocd_login = Mock(side_effect=LifecycleError("login failed"))

    with pytest.raises(LifecycleError, match="login failed"):
        with boot.argocd_session():
            pass

    assert boot.verified_server is None


def test_app_list_rejected_triggers_login(tmp_path, monkeypatch):
    """When account query passes but app list fails, login must be attempted."""
    from foundation import LifecycleError

    boot = RecoveryBootstrap(config(tmp_path), Mock())
    boot._argocd_cli = Path("/approved/argocd")
    monkeypatch.setenv("LAB_ARGOCD_SERVER", "127.0.0.1:8443")
    monkeypatch.setattr(boot, "_listening", lambda port: True)

    verify_calls = []

    def fail_app_list(cli, server):
        verify_calls.append(len(verify_calls))
        if len(verify_calls) == 1:
            # Simulate: get-user-info passes, app list fails
            raise LifecycleError("Command failed: argocd (exit=20; category=ARGOCD_AUTH)")

    boot._verify_argocd_auth = Mock(side_effect=fail_app_list)
    boot._argocd_login = Mock()

    with boot.argocd_session() as server:
        assert server == "127.0.0.1:8443"

    boot._argocd_login.assert_called_once()
    assert len(verify_calls) == 2


def test_verify_argocd_auth_checks_app_list_not_just_user_info(tmp_path, monkeypatch):
    """_verify_argocd_auth must call both get-user-info AND app list."""
    boot = RecoveryBootstrap(config(tmp_path), Mock())
    boot._argocd_cli = Path("/approved/argocd")
    cli = Path("/approved/argocd")
    server = "127.0.0.1:8443"

    calls = []

    def fake_run(args, **kwargs):
        calls.append(list(args))
        return subprocess.CompletedProcess(args, 0, "", "")

    boot.runner.run = Mock(side_effect=fake_run)
    boot._verify_argocd_auth(cli, server)

    assert len(calls) == 2
    assert "get-user-info" in " ".join(str(a) for a in calls[0])
    assert "app" in calls[1] and "list" in calls[1]
    assert "--server" in calls[0]
    assert "--server" in calls[1]


def test_admin_password_retrieved_from_k8s_secret(tmp_path, monkeypatch):
    """_argocd_admin_password must retrieve from argocd-initial-admin-secret."""
    import base64

    boot = RecoveryBootstrap(config(tmp_path), Mock())
    password_value = "test-admin-password-123"
    encoded = base64.b64encode(password_value.encode()).decode()

    def fake_capture(args, **kwargs):
        if "jsonpath" in " ".join(str(a) for a in args) and "argocd-initial-admin-secret" in " ".join(str(a) for a in args):
            return encoded
        return ""

    boot._capture = Mock(side_effect=fake_capture)
    result = boot._argocd_admin_password()
    assert result == password_value


def test_missing_admin_secret_fails_closed(tmp_path, monkeypatch):
    """Missing argocd-initial-admin-secret must fail closed."""
    boot = RecoveryBootstrap(config(tmp_path), Mock())
    boot._capture = Mock(return_value="")
    with pytest.raises(LifecycleError, match="initial admin secret is unavailable"):
        boot._argocd_admin_password()


def test_non_auth_error_never_triggers_admin_login(tmp_path, monkeypatch):
    from foundation import LifecycleError
    boot = RecoveryBootstrap(config(tmp_path), Mock())
    boot._argocd_cli = Path("/approved/argocd")
    monkeypatch.setenv("LAB_ARGOCD_SERVER", "127.0.0.1:8443")
    monkeypatch.setattr(boot, "_listening", lambda port: True)
    boot._verify_argocd_auth = Mock(side_effect=LifecycleError("PERMISSION_DENIED"))
    boot._argocd_login = Mock()
    with pytest.raises(LifecycleError, match="PERMISSION_DENIED"):
        with boot.argocd_session():
            pass
    boot._argocd_login.assert_not_called()


def test_login_no_password_argv_or_insecure(tmp_path, monkeypatch):
    boot = RecoveryBootstrap(config(tmp_path), Mock())
    boot._argocd_admin_password = Mock(return_value="synthetic-password")
    observed = []
    def fake_run(argv, **kw):
        observed.append((argv, kw))
        return subprocess.CompletedProcess(argv, 0, "", "")
    monkeypatch.setattr(subprocess, "run", fake_run)
    boot._argocd_login(Path("/approved/argocd"), "127.0.0.1:8443")
    argv, kw = observed[0]
    assert "--password-stdin" in argv
    assert "--insecure" not in argv
    assert "--password" not in argv
    assert "synthetic-password" not in " ".join(argv)
    assert kw["input"] == "synthetic-password"


def test_unsupported_stdin_fails_without_fallback(tmp_path, monkeypatch):
    from foundation import LifecycleError
    boot = RecoveryBootstrap(config(tmp_path), Mock())
    boot._argocd_admin_password = Mock(return_value="synthetic-password")
    calls = []
    def fake_run(argv, **kw):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 1, "", "unknown flag: --password-stdin")
    monkeypatch.setattr(subprocess, "run", fake_run)
    with pytest.raises(LifecycleError, match="lacks secure password-stdin"):
        boot._argocd_login(Path("/approved/argocd"), "127.0.0.1:8443")
    assert len(calls) == 1
