"""Offline security regression tests for native PostgreSQL restore transport."""
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'scripts/lifecycle'))
import recovery as r
import lifecycle as n
from foundation import LifecycleError


def test_native_commands_enforce_verified_tls():
    source = Path(r.__file__).read_text()
    assert 'PGSSLMODE=require' not in source
    for credential in ('MASTER_PASSWORD', 'APP_PASSWORD'):
        script = r._verified_pg_env(credential)
        assert 'PGSSLMODE=verify-full' in script
        assert 'test -r "$PGSSLROOTCERT"' in script
        assert 'PGPASSWORD=' in script
    with pytest.raises(LifecycleError):
        r._verified_pg_env('NOT_APPROVED')
    assert source.count('_verified_pg_env(') >= 5


def test_official_ca_source_is_pinned_and_authenticated():
    assert r.RDS_CA_OFFICIAL_URL == 'https://truststore.pki.rds.amazonaws.com/global/global-bundle.pem'
    class Redirect:
        status = 200
        def __enter__(self): return self
        def __exit__(self, *_): return None
        def geturl(self): return 'https://other.example/bundle.pem'
        def read(self, *_): return b'fake'
    with patch('urllib.request.urlopen', return_value=Redirect()):
        with pytest.raises(LifecycleError, match='official AWS URL'):
            r._rds_ca_bundle()


def test_pg_client_mount_append_keeps_existing_settings():
    pod = n.client_pod(SimpleNamespace(), 'test-restore')
    pod['spec']['volumes'] = [{'name': 'existing', 'emptyDir': {}}]
    pod['spec']['containers'][0]['volumeMounts'] = [{'name': 'existing', 'mountPath': '/existing'}]
    pod['spec']['volumes'].append({'name': 'rds-ca', 'configMap': {'name': 'private-ca'}})
    pod['spec']['containers'][0]['volumeMounts'].append({'name': 'rds-ca', 'mountPath': '/etc/ssl/rds'})
    assert len(pod['spec']['volumes']) == 2
    assert len(pod['spec']['containers'][0]['volumeMounts']) == 2


def test_password_not_in_psql_arguments_even_with_special_characters():
    from subprocess import run
    import os
    password = "a 'quoted' password ; $HOME \\ \\n # ! :"
    sql = "\\getenv pwd APP_PASSWORD\nALTER ROLE app_runtime LOGIN PASSWORD :'pwd';\n"
    result = run(['sh', '-c', 'printf "%s" "$APP_PASSWORD"', 'sh'],
                 env={**os.environ, 'APP_PASSWORD': password}, capture_output=True, check=True)
    assert result.stdout.decode() == password
    assert password not in ' '.join(['sh', '-c', r._verified_pg_env('MASTER_PASSWORD')])
    assert '-v "pwd=$APP_PASSWORD"' not in Path(r.__file__).read_text()
    assert "\\getenv pwd APP_PASSWORD" in sql


def test_client_readonly_container_remains_unprivileged():
    pod = n.client_pod(SimpleNamespace(), 'test-restore')
    assert pod['spec']['automountServiceAccountToken'] is False
    assert pod['spec']['containers'][0]['securityContext']['readOnlyRootFilesystem'] is True


def test_cleanup_rejects_delete_or_absence_proof_failure():
    config = SimpleNamespace(environment=lambda: {})
    with patch.object(r, 'call', side_effect=LifecycleError('delete refused')), patch.object(r, 'output', return_value='pod/existing'):
        assert r._cleanup_restore_resources(config, 'unique-pod', 'unique-secret') is False
