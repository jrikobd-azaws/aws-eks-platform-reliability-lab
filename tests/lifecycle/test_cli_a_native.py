"""CLI A policy and non-destructive regression tests."""
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
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


def test_check_db_fresh_is_read_only():
    from foundation import LabConfig
    config = LabConfig()
    response = json.dumps({'DBInstances': [{
        'InstanceCreateTime': '2026-10-22T00:00:00Z',
        'DBInstanceStatus': 'available',
        'Endpoint': {'Address': 'approved.rds.amazonaws.com'},
    }]}).encode()
    with patch.object(r, 'call', return_value=response) as invoked:
        r.check_db_fresh(
            config,
            {'rds_address': 'approved.rds.amazonaws.com'},
            {'created_utc': '2026-10-21T00:00:00Z'},
        )
    invoked.assert_called_once()
    argv = invoked.call_args.args[0]
    assert argv == [
        'aws', 'rds', 'describe-db-instances', '--db-instance-identifier',
        config.cluster_name + '-postgres', '--output', 'json',
    ]


def test_empty_target_query_matches_native_restore_contract():
    assert "pg_catalog" in r.EMPTY_TARGET_QUERY
    assert "information_schema" in r.EMPTY_TARGET_QUERY
    assert "pg_toast%" in r.EMPTY_TARGET_QUERY
    assert "pg_temp_%" in r.EMPTY_TARGET_QUERY
    assert "('r','p','v','m','S','f')" in r.EMPTY_TARGET_QUERY


def test_database_acceptance_query_covers_schema_ownership_and_dml():
    query = r._database_acceptance_query()
    assert r.EXPECTED_ALEMBIC_REVISION in query
    assert "to_regclass('public.user')" in query
    assert "to_regclass('public.item')" in query
    assert query.count("data_type='uuid'") == 2
    assert query.count("constraint_type='PRIMARY KEY'") == 2
    assert "src.attname='owner_id'" in query
    assert "dst.attname='id'" in query
    assert "fk.convalidated" in query
    assert "has_database_privilege" in query
    assert "pg_get_userbyid(datdba)<>current_user" in query
    assert query.count("has_table_privilege") == 8


def test_database_acceptance_rejects_incomplete_schema():
    execute = Mock(return_value=("|".join(('t',) * 20) + "|f").encode())
    with pytest.raises(LifecycleError, match="Alembic, schema, ownership"):
        r._validate_restored_database(execute)


def test_database_acceptance_accepts_complete_schema():
    complete = '|'.join(('t',) * 10 + ('f',) * 2 + ('t',) * 9).encode()
    execute = Mock(return_value=complete)
    r._validate_restored_database(execute)
    submitted = execute.call_args.args[0]
    assert 'app_runtime' in submitted
    assert 'fe56fa70289e' in submitted


def test_restore_cleanup_deletes_only_unique_resources_and_proves_absence():
    config = SimpleNamespace(environment=lambda: {})
    with patch.object(r, 'call') as delete, patch.object(r, 'output', return_value='') as get:
        assert r._cleanup_restore_resources(config, 'restore-pod-abc', 'restore-secret-abc')
    assert delete.call_count == 2
    assert get.call_count == 2
    assert {call.args[0][5] for call in delete.call_args_list} == {
        'restore-pod-abc', 'restore-secret-abc'
    }


def test_restore_cleanup_failure_is_not_accepted():
    config = SimpleNamespace(environment=lambda: {})
    with patch.object(r, 'call', side_effect=LifecycleError('delete failed')), \
         patch.object(r, 'output', return_value=''):
        assert not r._cleanup_restore_resources(
            config, 'restore-pod-abc', 'restore-secret-abc'
        )


def test_partial_restore_and_cleanup_failures_are_distinct():
    restore, cleanup = r._restore_failure_categories(
        LifecycleError('restore interrupted'),
        restore_attempted=True,
        archive_restore_completed=False,
        cleanup_verified=False,
    )
    assert restore == 'PARTIAL_RESTORE_OR_UNKNOWN'
    assert cleanup == 'TEMPORARY_RESOURCE_CLEANUP_INCOMPLETE'


def test_cleanup_only_failure_does_not_invent_restore_failure():
    restore, cleanup = r._restore_failure_categories(
        None,
        restore_attempted=True,
        archive_restore_completed=True,
        cleanup_verified=False,
    )
    assert restore is None
    assert cleanup == 'TEMPORARY_RESOURCE_CLEANUP_INCOMPLETE'


def test_database_preflight_does_not_require_backend_or_kubernetes(tmp_path):
    from foundation import LabConfig
    config = LabConfig(evidence_dir=tmp_path)
    restore_test = tmp_path / 'restore-test.json'
    restore_test.write_text('{}')
    backup = {
        'manifest': tmp_path / 'backup-manifest.json',
        'manifest_data': {'created_utc': '2026-10-21T00:00:00Z'},
        'values': {'rds_address': 'approved.rds.amazonaws.com'},
        'database': 'app',
        'host': 'approved.rds.amazonaws.com',
        'database_sha256': 'a' * 64,
        'source_revision': 'b' * 40,
        'recovery_controller_revision': 'c' * 40,
    }
    with patch.object(r, 'account_guard'), \
         patch('lifecycle.validate_backup_manifest', return_value=backup), \
         patch('lifecycle.discover_latest_restore_test', return_value=restore_test), \
         patch('lifecycle.verify_backup_restore_provenance'), \
         patch.object(r, 'check_db_fresh'), \
         patch.object(r, 'call') as command:
        report = r.preflight_database_restore(config)
    command.assert_not_called()
    evidence = json.loads(report.read_text())
    assert evidence['status'] == 'passed_pending_authorized_target_check'
    assert evidence['target_emptiness_verified'] is False
    assert evidence['restore_authorization_required'] == (
        'RESTORE-' + config.expected_account_id
    )


def test_native_database_preflight_cli_is_exposed_without_confirmation():
    source = (ROOT / 'scripts/lifecycle/labctl.py').read_text()
    assert "elif command=='native-database-preflight'" in source
    assert 'preflight_database_restore(config, args.report)' in source


def test_native_database_restore_keeps_account_bound_authorization():
    import labctl
    args = SimpleNamespace(report='/tmp/manifest.json', confirm='wrong')
    with pytest.raises(LifecycleError, match='Explicit RESTORE-account confirmation'):
        labctl.native_action('native-database-restore', args)

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
    config.evidence_dir = tmp_path
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
    config.evidence_dir = tmp_path
    monkeypatch.setattr(n, "BASE", tmp_path)
    monkeypatch.setattr(n, "account_guard", lambda c: None)
    calls = []
    monkeypatch.setattr(n, "call", lambda *a, **kw: calls.append(a))
    with pytest.raises(LifecycleError, match="Terraform inputs changed"):
        n.execute_plan(config, report, "REBUILD-275544662748", tfvars)
    assert not calls
