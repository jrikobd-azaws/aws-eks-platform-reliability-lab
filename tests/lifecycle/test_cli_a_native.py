"""CLI A policy and non-destructive regression tests."""
import json
import sys
from pathlib import Path
from unittest.mock import patch
import pytest
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'scripts/lifecycle'))
import lifecycle as n
import recovery as r
from foundation import LifecycleError

def resource(address,action,mode='managed'):
    return {'address':address,'mode':mode,'change':{'actions':action}}

def test_read_and_create_only():
    p={'resource_changes':[resource('module.eks.aws_eks_cluster.this',['create']),resource('module.eks.data.tls_certificate.cluster',['read'],'data')]}
    assert n.assert_plan_policy(p,[],action='create')['count']==1

def test_reject_retained_update():
    p={'resource_changes':[resource('aws_ecr_repository.stage3["backend"]',['update'])]}
    with pytest.raises(LifecycleError): n.assert_plan_policy(p,[],action='create')

def test_reject_replace():
    p={'resource_changes':[resource('module.eks.aws_eks_cluster.this',['delete','create'])]}
    with pytest.raises(LifecycleError): n.assert_plan_policy(p,[],action='create')

def test_exact_destroy_set():
    a='module.eks.aws_eks_cluster.this'
    p={'resource_changes':[resource(a,['delete'])]}
    assert n.assert_plan_policy(p,[a],action='delete')['count']==1
    with pytest.raises(LifecycleError): n.assert_plan_policy(p,[a,'module.rds.aws_db_instance.this'],action='delete')

def test_reject_secret_deletion():
    p={'resource_changes':[resource('aws_secretsmanager_secret.example',['delete'])]}
    with pytest.raises(LifecycleError): n.assert_plan_policy(p,['aws_secretsmanager_secret.example'],action='delete')

def test_tfvars_requires_private_permissions(tmp_path):
    p=tmp_path/'inputs.tfvars.json';p.write_text('{}');p.chmod(0o644)
    with pytest.raises(LifecycleError): n.checked_inputs(p)
    p.chmod(0o600)
    assert n.checked_inputs(p)==p.resolve()

def test_restore_refuses_old_rds():
    from foundation import LabConfig
    c=LabConfig()
    with patch.object(r,'call',return_value=json.dumps({'DBInstances':[{'InstanceCreateTime':'2020-01-01T00:00:00Z','DBInstanceStatus':'available','Endpoint':{'Address':'test'}}]}).encode()):
        with pytest.raises(LifecycleError): r.check_db_fresh(c,{'rds_address':'test'},{'created_utc':'2026-10-16T00:00:00Z'})

def test_no_aws_calls_in_policy():
    with patch.object(r,'call') as mocked:
        n.assert_plan_policy({'resource_changes':[resource('module.rds.aws_db_instance.this',['create'])]},[],action='create')
        mocked.assert_not_called()


def test_rebuild_execute_accepts_checked_private_tfvars_outside_repo(tmp_path, monkeypatch):
    import hashlib
    from types import SimpleNamespace
    import lifecycle as n

    private = tmp_path / "historical"
    private.mkdir()
    tfvars = private / "approved.tfvars.json"
    tfvars.write_text("{}")
    tfvars.chmod(0o600)
    saved = tmp_path / "terraform.tfplan"
    saved.write_bytes(b"synthetic-saved-plan")
    report = tmp_path / "plan.json"
    report.write_text(json.dumps({
        "kind": "rebuild", "account": "275544662748",
        "region": "eu-west-1", "cluster": "lab",
        "tfvars_sha256": hashlib.sha256(tfvars.read_bytes()).hexdigest(),
        "plan": str(saved),
        "plan_sha256": hashlib.sha256(saved.read_bytes()).hexdigest(),
        "state_lineage": "fixed-lineage", "state_serial": 60,
        "targets": [],
    }))
    config = SimpleNamespace(
        expected_account_id="275544662748", aws_region="eu-west-1",
        cluster_name="lab",
    )
    monkeypatch.setattr(n, "BASE", tmp_path)
    monkeypatch.setattr(n, "account_guard", lambda c: None)
    monkeypatch.setattr(n, "environment", lambda c: {})
    monkeypatch.setattr(n, "plan_json", lambda c, p: {
        "resource_changes": [{"address": "module.eks.aws_eks_cluster.this",
                              "mode": "managed", "change": {"actions": ["create"]}}],
    })
    calls = []
    def fake_call(argv, **kwargs):
        calls.append(list(map(str, argv)))
        if "pull" in argv:
            return json.dumps({"lineage": "fixed-lineage", "serial": 60}).encode()
        return b""
    monkeypatch.setattr(n, "call", fake_call)
    n.execute_plan(config, report, "REBUILD-275544662748", tfvars)
    assert len([c for c in calls if "apply" in c]) == 1

def test_rebuild_execute_rejects_changed_tfvars_before_apply(tmp_path, monkeypatch):
    import hashlib
    from types import SimpleNamespace
    import lifecycle as n
    tfvars = tmp_path / "approved.tfvars.json"
    tfvars.write_text("{}")
    tfvars.chmod(0o600)
    report = tmp_path / "plan.json"
    report.write_text(json.dumps({
        "kind": "rebuild", "account": "275544662748",
        "region": "eu-west-1", "cluster": "lab",
        "tfvars_sha256": hashlib.sha256(b'{"changed":true}').hexdigest(),
    }))
    config = SimpleNamespace(expected_account_id="275544662748",
                             aws_region="eu-west-1", cluster_name="lab")
    monkeypatch.setattr(n, "BASE", tmp_path)
    monkeypatch.setattr(n, "account_guard", lambda c: None)
    calls = []
    monkeypatch.setattr(n, "call", lambda *a, **kw: calls.append(a))
    with pytest.raises(LifecycleError, match="Terraform inputs changed"):
        n.execute_plan(config, report, "REBUILD-275544662748", tfvars)
    assert not calls
