"""Offline-only contract coverage for the LB3 coordinator."""
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'scripts/lifecycle'))
from foundation import LifecycleError
from orchestrate import OneCommandLifecycle, OperatorApprovals


class FakeAdapter:
    def __init__(self, root):
        self.root = root
        self.calls = []
        self.fail = None
        self.infra = True
        self.db = False
        self.platform = False
        self.app = False
        self.bad_source = False

    def mark(self, name):
        self.calls.append(name)
        if self.fail == name:
            raise LifecycleError(name + ' deliberately failed')

    def check_identity(self):
        self.mark('identity')

    def check_shutdown_identity(self):
        self.mark("shutdown_identity")

    def verify_live_app(self):
        self.mark('live_app')
        if self.fail == 'readiness':
            raise LifecycleError('readiness failed')

    def new_backup(self):
        self.mark('backup')
        p = self.root / 'backup.json'
        p.write_text('{}')
        p.chmod(0o600)
        return p

    def verify_backup(self, manifest):
        self.mark('verify_backup')
        if self.bad_source:
            raise LifecycleError('source revision mismatch')
        return {'database_sha256': 'a' * 64}

    def restore_test(self, manifest, confirmation):
        self.mark('restore_test')
        p = self.root / 'restore-test.json'
        p.write_text('{}')
        p.chmod(0o600)
        return p

    def verify_restore_test(self, manifest):
        self.mark('verify_restore_test')
        return self.root / 'restore-test.json'

    def plan(self, kind, tfvars):
        self.mark('plan_' + kind)
        p = self.root / ('plan-' + kind + '.json')
        p.write_text(json.dumps({'plan_sha256': 'b' * 64}))
        p.chmod(0o600)
        return p

    def verify_plan(self, plan, tfvars, kind):
        return self.fail != "state_changed"

    def verify_shutdown_certification(self, plan):
        return True

    def certify(self, plan, tfvars, confirmation):
        self.mark('certify')
        p = self.root / 'readiness.json'
        p.write_text('{}')
        p.chmod(0o600)
        return p

    def apply(self, plan, tfvars, confirmation):
        self.mark('apply')
        self.infra = (confirmation.startswith('REBUILD-'))

    def verify_shutdown_complete(self, plan):
        return not self.infra

    def infrastructure_ready(self):
        self.mark('infrastructure_ready')
        return self.infra

    def platform_ready(self):
        return self.platform

    def database_ready(self):
        return self.db

    def restore_platform(self):
        self.mark('platform')
        self.platform = True

    def verify_recovery_point_without_live_rds(self, manifest):
        self.verify_backup(manifest)
        self.verify_restore_test(manifest)

    def preflight_db(self, manifest):
        self.mark('preflight_db')
        if self.fail == 'nonempty':
            raise LifecycleError('Target database is not empty')

    def restore_db(self, manifest):
        self.mark('database')
        self.db = True

    def verify_final(self):
        self.mark('final')
        self.verify_live_app()


@pytest.fixture
def harness(tmp_path):
    config = SimpleNamespace(evidence_dir=tmp_path, expected_account_id='275544662748')
    adapter = FakeAdapter(tmp_path)
    return OneCommandLifecycle(config, adapter), adapter, tmp_path


def test_default_preflight_no_live_operations(harness):
    ctl, adapter, _ = harness
    assert ctl.shutdown(phase='preflight')['live_execution'] is False
    assert ctl.restore(phase='preflight')['live_execution'] is False
    assert adapter.calls == []


def test_missing_backup_approval_blocks_all_mutation(harness):
    ctl, adapter, _ = harness
    with pytest.raises(LifecycleError, match='backup authorisation'):
        ctl.shutdown(phase='prepare', tfvars='reviewed')
    assert 'backup' not in adapter.calls


def test_backup_failure_blocks_shutdown(harness):
    ctl, adapter, _ = harness
    adapter.fail = 'backup'
    with pytest.raises(LifecycleError, match='backup deliberately failed'):
        ctl.shutdown(phase='prepare', tfvars='reviewed', approvals=OperatorApprovals(backup='BACKUP-275544662748'))
    assert not any(x.startswith('plan_') for x in adapter.calls)
    assert 'apply' not in adapter.calls
    with pytest.raises(LifecycleError, match='Ambiguous previous backup'):
        ctl.shutdown(phase='prepare', tfvars='reviewed', approvals=OperatorApprovals(backup='BACKUP-275544662748'))


def test_invalid_backup_source_blocks_restore_test(harness):
    ctl, adapter, _ = harness
    adapter.bad_source = True
    with pytest.raises(LifecycleError, match='source revision mismatch'):
        ctl.shutdown(phase='prepare', tfvars='reviewed', approvals=OperatorApprovals(backup='BACKUP-275544662748'))
    assert 'restore_test' not in adapter.calls


def prepare_shutdown(harness):
    ctl, adapter, root = harness
    result = ctl.shutdown(phase='prepare', tfvars='reviewed', approvals=OperatorApprovals(
        backup='BACKUP-275544662748', restore_test='RESTORE-TEST-275544662748-aaaaaaaaaaaa'))
    return result


def test_shutdown_prepare_stops_before_apply(harness):
    result = prepare_shutdown(harness)
    ctl, adapter, _ = harness
    assert result['phase'] == 'prepared'
    assert 'apply' not in adapter.calls


def test_shutdown_execute_requires_certification_and_destroy_approval(harness):
    result = prepare_shutdown(harness)
    ctl, adapter, _ = harness
    with pytest.raises(LifecycleError, match='certification authorisation'):
        ctl.shutdown(phase='execute', tfvars='reviewed', report=result['plan'])
    assert 'apply' not in adapter.calls
    with pytest.raises(LifecycleError, match='shutdown authorisation'):
        ctl.shutdown(phase='execute', tfvars='reviewed', report=result['plan'], approvals=OperatorApprovals(certification='CERTIFY-275544662748-bbbbbbbbbbbb'))
    assert 'apply' not in adapter.calls


def test_shutdown_execute_success_and_nonrepeat(harness):
    result = prepare_shutdown(harness)
    ctl, adapter, _ = harness
    a = OperatorApprovals(certification='CERTIFY-275544662748-bbbbbbbbbbbb', shutdown='SHUTDOWN-275544662748')
    assert ctl.shutdown(phase='execute', tfvars='reviewed', report=result['plan'], approvals=a)['verified']
    assert adapter.calls.count('apply') == 1
    assert ctl.shutdown(phase='execute', tfvars='reviewed', report=result['plan'], approvals=a)['verified']
    assert adapter.calls.count('apply') == 1


def test_restore_requires_manifest_even_in_prepare(harness):
    ctl, adapter, _ = harness
    with pytest.raises(LifecycleError, match='Explicit recovery manifest'):
        ctl.restore(phase='prepare', tfvars='reviewed')
    assert 'apply' not in adapter.calls


def prepare_restore(harness):
    ctl, adapter, root = harness
    p = root / 'recovery-manifest.json'
    p.write_text('{}')
    p.chmod(0o600)
    result = ctl.restore(phase='prepare', tfvars='reviewed', manifest=str(p))
    return p, result


def test_restore_prepare_never_applies(harness):
    manifest, result = prepare_restore(harness)
    assert result['phase'] == 'prepared'
    assert 'apply' not in harness[1].calls


def test_restore_missing_approval_refuses_rebuild(harness):
    manifest, result = prepare_restore(harness)
    ctl, adapter, _ = harness
    with pytest.raises(LifecycleError, match='rebuild authorisation'):
        ctl.restore(phase='execute', tfvars='reviewed', manifest=str(manifest), report=result['plan'])
    assert 'apply' not in adapter.calls


def test_restore_rechecks_manifest(harness):
    manifest, result = prepare_restore(harness)
    ctl, adapter, _ = harness
    adapter.bad_source = True
    with pytest.raises(LifecycleError, match='source revision mismatch'):
        ctl.restore(phase='execute', tfvars='reviewed', manifest=str(manifest), report=result['plan'],
                    approvals=OperatorApprovals(rebuild='REBUILD-275544662748'))
    assert 'apply' not in adapter.calls


def test_restore_nonempty_db_prevents_replay(harness):
    manifest, result = prepare_restore(harness)
    ctl, adapter, _ = harness
    adapter.fail = 'nonempty'
    with pytest.raises(LifecycleError, match='not empty'):
        ctl.restore(phase='execute', tfvars='reviewed', manifest=str(manifest), report=result['plan'],
                    approvals=OperatorApprovals(rebuild='REBUILD-275544662748', gitops='GITOPS-275544662748', database='RESTORE-275544662748'))
    assert 'database' not in adapter.calls


def test_restore_missing_gitops_approval_blocks_sync(harness):
    manifest, result = prepare_restore(harness)
    ctl, adapter, _ = harness
    with pytest.raises(LifecycleError, match='GitOps recovery authorisation'):
        ctl.restore(phase='execute', tfvars='reviewed', manifest=str(manifest), report=result['plan'],
                    approvals=OperatorApprovals(rebuild='REBUILD-275544662748'))
    assert 'platform' not in adapter.calls


def test_restore_final_readiness_failure_never_accepts(harness):
    manifest, result = prepare_restore(harness)
    ctl, adapter, _ = harness
    adapter.fail = 'readiness'
    with pytest.raises(LifecycleError, match='readiness failed'):
        ctl.restore(phase='execute', tfvars='reviewed', manifest=str(manifest), report=result['plan'],
                    approvals=OperatorApprovals(rebuild='REBUILD-275544662748',gitops='GITOPS-275544662748', database='RESTORE-275544662748'))
    assert ctl._load().get('completed') is not True


def test_checkpoint_invalid_permissions_rejected(harness):
    ctl, _, root = harness
    ctl.preflight('shutdown')
    ctl.record.write_text('{"schema":1,"stages":{}}')
    ctl.record.chmod(0o644)
    with pytest.raises(LifecycleError, match='Unsafe LB3'):
        ctl.preflight('shutdown')


@pytest.mark.parametrize('stage,operation', [
    ('backup', 'shutdown'), ('restore_test', 'shutdown'),
    ('shutdown_plan', 'shutdown'), ('shutdown_certification', 'shutdown'),
    ('shutdown_apply', 'shutdown'), ('rebuild_plan', 'restore'),
    ('rebuild_apply', 'restore'), ('platform', 'restore'),
    ('database', 'restore'), ('application', 'restore'),
])
def test_reconcile_interrupted_stages_fail_closed_without_approval(harness, stage, operation):
    ctl, adapter, _ = harness
    state = ctl._state(operation)
    state['stages'][stage] = {'status': 'running', 'time': 'before interruption'}
    ctl._save(state)
    before = dict(ctl._load()['stages'][stage])
    with pytest.raises(LifecycleError, match='authorisation required'):
        ctl.reconcile(operation, stage=stage, approval=None)
    assert ctl._load()['stages'][stage] == before
    assert adapter.calls == []


def test_reconcile_backup_from_private_evidence_does_not_replay(harness):
    ctl, adapter, root = harness
    ctl._select('shutdown')
    state = ctl._state('shutdown')
    state['stages']['backup'] = {'status': 'failed', 'time': 'interrupted'}
    ctl._save(state)
    artifact = root / 'backup.json'
    artifact.write_text('{}')
    artifact.chmod(0o600)
    challenge = ctl.preflight('shutdown')['reconciliation_challenges']['backup']
    result = ctl.reconcile('shutdown', stage='backup', approval=challenge, evidence=str(artifact))
    assert result['action_replayed'] is False
    assert ctl._load()['stages']['backup']['status'] == 'passed'
    assert 'backup' not in adapter.calls
    assert 'verify_backup' in adapter.calls


def test_reconcile_backup_rejects_wrong_source_and_preserves_failure(harness):
    ctl, adapter, root = harness
    state = ctl._state('shutdown')
    state['stages']['backup'] = {'status': 'failed'}
    ctl._save(state)
    artifact = root / 'backup.json'
    artifact.write_text('{}')
    artifact.chmod(0o600)
    adapter.bad_source = True
    challenge = ctl.preflight('shutdown')['reconciliation_challenges']['backup']
    with pytest.raises(LifecycleError, match='source revision mismatch'):
        ctl.reconcile('shutdown', stage='backup', approval=challenge, evidence=str(artifact))
    assert ctl._load()['stages']['backup']['status'] == 'failed'


def test_reconcile_refuses_symlink_evidence(harness):
    ctl, adapter, root = harness
    state = ctl._state('shutdown')
    state['stages']['backup'] = {'status': 'failed'}
    ctl._save(state)
    artifact = root / 'backup.json'
    artifact.write_text('{}')
    artifact.chmod(0o600)
    link = root / 'link.json'
    link.symlink_to(artifact)
    challenge = ctl.preflight('shutdown')['reconciliation_challenges']['backup']
    with pytest.raises(LifecycleError, match='nonsymlink'):
        ctl.reconcile('shutdown', stage='backup', approval=challenge, evidence=str(link))
    assert ctl._load()['stages']['backup']['status'] == 'failed'


def test_reconcile_successful_shutdown_apply_does_not_reapply(harness):
    ctl, adapter, root = harness
    state = ctl._state('shutdown')
    plan = root / 'plan.json'
    plan.write_text('{}')
    plan.chmod(0o600)
    state['prepared_plan'] = str(plan)
    state['stages']['shutdown_apply'] = {'status': 'running'}
    ctl._save(state)
    adapter.infra = False
    challenge = ctl.preflight('shutdown')['reconciliation_challenges']['shutdown_apply']
    result = ctl.reconcile('shutdown', stage='shutdown_apply', approval=challenge)
    assert result['action_replayed'] is False
    assert 'apply' not in adapter.calls
    assert ctl._load()['stages']['shutdown_apply']['reconciled'] is True


def test_reconcile_shutdown_apply_rejects_live_targets_remaining(harness):
    ctl, adapter, root = harness
    state = ctl._state('shutdown')
    plan = root / 'plan.json'
    plan.write_text('{}')
    plan.chmod(0o600)
    state['prepared_plan'] = str(plan)
    state['stages']['shutdown_apply'] = {'status': 'failed'}
    ctl._save(state)
    challenge = ctl.preflight('shutdown')['reconciliation_challenges']['shutdown_apply']
    with pytest.raises(LifecycleError, match='not independently complete'):
        ctl.reconcile('shutdown', stage='shutdown_apply', approval=challenge)
    assert 'apply' not in adapter.calls
    assert ctl._load()['stages']['shutdown_apply']['status'] == 'failed'


def test_reconcile_nonempty_or_incomplete_database_blocks_promotion(harness):
    ctl, adapter, root = harness
    state = ctl._state('restore')
    state['stages']['database'] = {'status': 'failed'}
    ctl._save(state)
    challenge = ctl.preflight('restore')['reconciliation_challenges']['database']
    with pytest.raises(LifecycleError, match='not independently restored'):
        ctl.reconcile('restore', stage='database', approval=challenge)
    assert 'database' not in adapter.calls
    assert ctl._load()['stages']['database']['status'] == 'failed'


def test_reconcile_database_passes_only_if_current_database_ready(harness):
    ctl, adapter, root = harness
    state = ctl._state('restore')
    state['stages']['database'] = {'status': 'running'}
    ctl._save(state)
    adapter.db = True
    challenge = ctl.preflight('restore')['reconciliation_challenges']['database']
    result = ctl.reconcile('restore', stage='database', approval=challenge)
    assert result['action_replayed'] is False
    assert 'database' not in adapter.calls
    assert ctl._load()['stages']['database']['status'] == 'passed'


def test_reconciliation_challenge_invalidated_after_checkpoint_change(harness):
    ctl, adapter, root = harness
    state = ctl._state('restore')
    state['stages']['database'] = {'status': 'running', 'time': 'old'}
    ctl._save(state)
    challenge = ctl.preflight('restore')['reconciliation_challenges']['database']
    state['stages']['database']['time'] = 'new'
    ctl._save(state)
    adapter.db = True
    with pytest.raises(LifecycleError, match='authorisation required'):
        ctl.reconcile('restore', stage='database', approval=challenge)
    assert ctl._load()['stages']['database']['status'] == 'running'
