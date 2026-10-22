"""Checkpoint-aware AWS EKS recovery orchestration.

Infrastructure, database and platform recovery are independently
recoverable stages. Checkpoints never replace live validation.
"""

from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from bootstrap import RecoveryBootstrap
from foundation import (
    ROOT,
    CheckpointStore,
    CommandRunner,
    LabConfig,
    LifecycleError,
    atomic_json_write,
    utc_now,
    verify_account,
)


STAGES = (
    "infrastructure",
    "platform",
    "database",
    "verification",
)


@dataclass
class RecoveryManager:
    config: LabConfig
    runner: CommandRunner
    checkpoint: CheckpointStore
    bootstrap: RecoveryBootstrap | None = None

    def command(self, args, timeout=900):
        return self.runner.run(args, timeout=timeout)

    def capture(self, args, timeout=900):
        return self.command(args, timeout).stdout.strip()

    def account_guard(self):
        verify_account(
            self.runner,
            self.config.expected_account_id,
        )

    def terraform_outputs(self):
        raw = self.capture([
            "terraform",
            f"-chdir={self.config.terraform_dir}",
            "output",
            "-json",
        ])

        values = json.loads(raw)

        return {
            key: value["value"]
            for key, value in values.items()
        }

    def infrastructure_ready(self):
        """Verify actual AWS resources, not checkpoint status."""
        outputs = self.terraform_outputs()

        if outputs.get("cluster_name") != self.config.cluster_name:
            return False

        cluster = self.config.cluster_name

        eks = self.capture([
            "aws", "eks", "describe-cluster",
            "--name", cluster,
            "--query", "cluster.status",
            "--output", "text",
        ])

        rds = self.capture([
            "aws", "rds", "describe-db-instances",
            "--db-instance-identifier", cluster + "-postgres",
            "--query", "DBInstances[0].DBInstanceStatus",
            "--output", "text",
        ])

        return eks == "ACTIVE" and rds == "available"

    def platform_ready(self):
        """Validate the Argo-owned platform and recovery dependencies."""
        from recover_platform import platform_recovery_state_ready

        return platform_recovery_state_ready(self.config)

    def database_ready(self, *, deep=False):
        """Use actual PostgreSQL state during execution and evidence in preflight."""
        if deep:
            from recover_platform import validate_recovered_database

            return validate_recovered_database(self.config)

        report = (
            self.config.evidence_dir
            / "python-acceptance-20261016T024639Z"
            / "acceptance.json"
        )

        if not report.is_file():
            return False

        data = json.loads(report.read_text())

        return (
            data.get("fastapi_rds_live_validator") == "passed"
            and data.get("platform_verification") == "passed"
        )

    def verification_ready(self, *, deep=False):
        if deep:
            from recover_platform import validate_recovered_database, validate_recovered_application
            if not validate_recovered_database(self.config):
                return False
            return validate_recovered_application(self.config)
        # Historical reports are never sufficient for final certification.
        return False

    def validate(self, stage, *, deep=False):
        checks = {
            "infrastructure": self.infrastructure_ready,
            "platform": self.platform_ready,
            "database": lambda: self.database_ready(deep=deep),
            "verification": lambda: self.verification_ready(deep=deep),
        }

        return checks[stage]()

    def restore_infrastructure(self):
        """Prepare a rebuild plan without blindly applying it.

        The actual Terraform apply requires a separately reviewed
        create-only plan. Never infer that a missing cluster means
        the retained ECR/S3 resources should be recreated.
        """
        raise LifecycleError(
            "Infrastructure apply requires a separately validated "
            "Terraform create-only plan. Use the existing approved "
            "rebuild procedure; do not apply an unreviewed plan."
        )

    def restore_platform(self):
        script = ROOT / "scripts/lifecycle/recover_platform.py"

        if not script.is_file():
            raise LifecycleError("Platform recovery script missing")

        self.command([
            "python3", script,
        ], timeout=3600)

    def restore_database(self):
        """Guarded database restore using the existing native CLI.

        Approval and recovery manifest must be explicitly supplied
        by the operator; no approval is generated automatically.
        """
        manifest = os.environ.get("LAB_RECOVERY_MANIFEST", "")
        confirmation = os.environ.get("LAB_DATABASE_RESTORE_CONFIRM", "")

        if not manifest or not confirmation:
            raise LifecycleError(
                "Explicit recovery manifest and database restore "
                "confirmation are required"
            )

        expected = "RESTORE-" + self.config.expected_account_id
        if confirmation != expected:
            raise LifecycleError(
                "Database restore confirmation mismatch"
            )

        manifest_path = Path(manifest)
        if manifest_path.is_symlink() or not manifest_path.is_file():
            raise LifecycleError(
                "Recovery manifest missing or invalid"
            )

        boundary = self.config.evidence_dir.resolve()
        resolved = manifest_path.resolve()

        if not resolved.is_relative_to(boundary):
            raise LifecycleError(
                "Recovery manifest outside approved evidence directory"
            )

        self.command([
            "python3",
            str(ROOT / "scripts/lifecycle/labctl.py"),
            "--report", str(resolved),
            "--confirm", confirmation,
            "native-database-restore",
        ], timeout=3600)

    def verify_environment(self):
        """After database SQL validation, restore Stage 3 and verify application health."""
        from recover_platform import PlatformRecovery, validate_recovered_database, validate_recovered_application
        if not validate_recovered_database(self.config):
            raise LifecycleError("Database schema not ready for application recovery")
        manager = PlatformRecovery(
            self.config,
            CheckpointStore(self.config.evidence_dir / "python-platform-recovery-checkpoint.json"),
        )
        manager.verify_identity(update_kubeconfig=False)
        manager.run_stage(
            "stage3_application",
            manager.stage3_application_ready,
            manager.restore_stage3_application,
        )
        if not validate_recovered_application(self.config):
            raise LifecycleError("Stage 3 application readiness verification failed")

    def run_stage(
        self,
        stage: str,
        action: Callable[[], None],
        *,
        execute: bool,
    ):
        if stage not in STAGES:
            raise LifecycleError("Unknown recovery stage")

        # Revalidate actual state even when a checkpoint says passed. Unknown
        # state is an error, not permission to run a recovery action.
        already_ready = self.validate(stage, deep=True)

        if already_ready:
            self.checkpoint.update(
                stage, "passed",
                detail="Revalidated existing recovery state",
            )
            print(f"PASS: {stage} already ready")
            return

        if not execute:
            print(f"NEEDS_RECOVERY: {stage}")
            return

        self.checkpoint.update(stage, "running")

        try:
            action()

            if not self.validate(stage, deep=True):
                raise LifecycleError(
                    f"{stage} action completed but acceptance failed"
                )

            self.checkpoint.update(stage, "passed")
            print(f"PASS: {stage}")

        except Exception:
            self.checkpoint.update(stage, "failed")
            raise

    def resume(self, *, execute=False):
        self.account_guard()
        if self.bootstrap:
            identity = self.bootstrap.prepare(auto_repair=execute)
            reused = self.checkpoint.reconcile_identity(
                identity.checkpoint_identity()
            )
            state = "reused" if reused else "established"
            print(f"INFO: recovery checkpoint identity {state}", flush=True)

        actions = {
            "infrastructure": self.restore_infrastructure,
            "platform": self.restore_platform,
            "database": self.restore_database,
            "verification": self.verify_environment,
        }

        results = {}

        for stage in STAGES:
            if not execute:
                ready = self.validate(stage, deep=False)

                results[stage] = (
                    "ready" if ready else "requires_recovery"
                )
                print(f"{stage}: {results[stage]}")
                continue

            # Never proceed if the preceding stage remains unready.
            index = STAGES.index(stage)

            for prerequisite in STAGES[:index]:
                if not self.validate(prerequisite, deep=True):
                    raise LifecycleError(
                        f"Prerequisite not ready: {prerequisite}"
                    )

            self.run_stage(
                stage,
                actions[stage],
                execute=True,
            )

            results[stage] = "passed"

        report = {
            "timestamp_utc": utc_now(),
            "mode": "execute" if execute else "preflight",
            "stages": results,
            "note": (
                "Execute mode revalidates RDS identity, Alembic head, schema, "
                "runtime-role protection and application health. Preflight "
                "remains non-mutating and uses accepted evidence."
            ),
        }

        atomic_json_write(
            self.config.evidence_dir
            / "python-resume-latest.json",
            report,
        )

        return report

# CLI_A_NATIVE_V2
import datetime as dt
import json
import re
import secrets
import sys
from foundation import ROOT, LifecycleError
from lifecycle import call, output, environment, account_guard, terraform_values, sha256_file, database_connection, master_credentials, age_identity, client_pod
def check_db_fresh(config,values,manifest):
    raw=json.loads(call(['aws','rds','describe-db-instances','--db-instance-identifier',config.cluster_name+'-postgres','--output','json'],env=environment(config)))['DBInstances'][0]
    created=dt.datetime.fromisoformat(raw['InstanceCreateTime'].replace('Z','+00:00'))
    checkpoint=dt.datetime.fromisoformat(manifest['created_utc'].replace('Z','+00:00'))
    if created<=checkpoint or raw['DBInstanceStatus']!='available' or raw['Endpoint']['Address']!=values['rds_address']:
        raise LifecycleError('Database is not a newly recreated available RDS instance')

RDS_CA_CONTAINER_PATH = '/etc/ssl/rds/global-bundle.pem'


def _verified_pg_env(password_variable):
    """Fail closed unless the mounted CA and libpq TLS settings are usable."""
    if password_variable not in ('MASTER_PASSWORD', 'APP_PASSWORD'):
        raise LifecycleError('Unexpected PostgreSQL credential variable')
    return (
        'test -r "$PGSSLROOTCERT" && test -s "$PGSSLROOTCERT" || exit 78; '
        'export PGPASSWORD="${' + password_variable + '}"; '
        'export PGSSLMODE=verify-full; '
    )


RDS_CA_OFFICIAL_URL = 'https://truststore.pki.rds.amazonaws.com/global/global-bundle.pem'


def _rds_ca_bundle():
    """Fetch RDS trust directly from Amazon over authenticated HTTPS.

    Fail closed: redirect targets and unauthenticated local PEMs are refused.
    """
    import ssl
    import urllib.request
    try:
        request = urllib.request.Request(RDS_CA_OFFICIAL_URL, headers={'User-Agent': 'lb2-rds-trust/1'})
        with urllib.request.urlopen(request, timeout=20, context=ssl.create_default_context()) as response:
            if response.geturl() != RDS_CA_OFFICIAL_URL or response.status != 200:
                raise LifecycleError('RDS CA trust source did not match official AWS URL')
            content = response.read(131073)
        if len(content) > 131072 or content.count(b'-----BEGIN CERTIFICATE-----') < 3:
            raise LifecycleError('RDS CA trust bundle size or certificate count invalid')
        import tempfile
        with tempfile.NamedTemporaryFile() as tmp:
            tmp.write(content)
            tmp.flush()
            ssl.create_default_context(cafile=tmp.name)
        return content
    except (OSError, ssl.SSLError, ValueError) as exc:
        raise LifecycleError('Official AWS RDS CA retrieval or validation failed') from exc


EXPECTED_ALEMBIC_REVISION = 'fe56fa70289e'
EMPTY_TARGET_QUERY = (
    "SELECT COUNT(*) FROM pg_class c JOIN pg_namespace n "
    "ON n.oid=c.relnamespace WHERE n.nspname NOT IN "
    "('pg_catalog','information_schema') "
    "AND n.nspname NOT LIKE 'pg_toast%' "
    "AND n.nspname NOT LIKE 'pg_temp_%' "
    "AND c.relkind IN ('r','p','v','m','S','f');"
)


def _restore_failure_categories(
    failure, *, restore_attempted, archive_restore_completed, cleanup_verified
):
    restore_category = None
    if failure is not None:
        if not restore_attempted:
            restore_category = 'PRE_RESTORE_FAILURE'
        elif not archive_restore_completed:
            restore_category = 'PARTIAL_RESTORE_OR_UNKNOWN'
        else:
            restore_category = 'POST_RESTORE_VALIDATION_FAILED'
    cleanup_category = (
        None if cleanup_verified else 'TEMPORARY_RESOURCE_CLEANUP_INCOMPLETE'
    )
    return restore_category, cleanup_category


def _database_acceptance_query():
    """Return the post-restore schema and runtime-role acceptance query."""
    dml = [
        "has_table_privilege(current_user, 'public.\"%s\"', '%s')"
        % (table, privilege)
        for table in ('user', 'item')
        for privilege in ('SELECT', 'INSERT', 'UPDATE', 'DELETE')
    ]
    primary_key = lambda table: (
        "EXISTS (SELECT 1 FROM information_schema.table_constraints tc "
        "JOIN information_schema.key_column_usage kcu "
        "ON kcu.constraint_schema=tc.constraint_schema "
        "AND kcu.constraint_name=tc.constraint_name "
        "WHERE tc.table_schema='public' AND tc.table_name='" + table + "' "
        "AND tc.constraint_type='PRIMARY KEY' AND kcu.column_name='id')"
    )
    owner_fk = (
        "EXISTS (SELECT 1 FROM information_schema.referential_constraints rc "
        "JOIN information_schema.key_column_usage kcu "
        "ON kcu.constraint_schema=rc.constraint_schema "
        "AND kcu.constraint_name=rc.constraint_name "
        "JOIN information_schema.constraint_column_usage ccu "
        "ON ccu.constraint_schema=rc.unique_constraint_schema "
        "AND ccu.constraint_name=rc.unique_constraint_name "
        "WHERE kcu.table_schema='public' AND kcu.table_name='item' "
        "AND kcu.column_name='owner_id' AND ccu.table_schema='public' "
        "AND ccu.table_name='user' AND ccu.column_name='id')"
    )
    return (
        "SELECT current_user='app_runtime',"
        "(SELECT version_num='" + EXPECTED_ALEMBIC_REVISION + "' FROM alembic_version),"
        "to_regclass('public.user') IS NOT NULL,"
        "to_regclass('public.item') IS NOT NULL,"
        "(SELECT data_type='uuid' FROM information_schema.columns "
        "WHERE table_schema='public' AND table_name='user' AND column_name='id'),"
        "(SELECT data_type='uuid' FROM information_schema.columns "
        "WHERE table_schema='public' AND table_name='item' AND column_name='id'),"
        + primary_key('user') + "," + primary_key('item') + "," + owner_fk + ","
        "has_schema_privilege(current_user,'public','USAGE'),"
        "has_schema_privilege(current_user,'public','CREATE'),"
        "has_database_privilege(current_user,current_database(),'CREATE'),"
        "(SELECT pg_get_userbyid(datdba)<>current_user FROM pg_database "
        "WHERE datname=current_database()),"
        + ",".join(dml) + ";"
    )


def _validate_restored_database(exec_pg):
    query = _database_acceptance_query()
    script = (
        _verified_pg_env('APP_PASSWORD') +
        'exec psql -X -At -v ON_ERROR_STOP=1 -h "$1" '
        '-U app_runtime -d "$3" -c ' + __import__('shlex').quote(query)
    )
    expected = '|'.join(('t',) * 10 + ('f',) * 2 + ('t',) * 9)
    if exec_pg(script).decode().strip() != expected:
        raise LifecycleError(
            'Restored Alembic, schema, ownership or runtime permission validation failed'
        )


def _cleanup_restore_resources(config, pod_name, secret_name):
    """Delete only this restore's unique resources and prove their absence."""
    clean = True
    for kind, name in (('pod', pod_name), ('secret', secret_name)):
        try:
            call([
                'kubectl', '-n', 'dev', 'delete', kind, name,
                '--ignore-not-found=true', '--wait=true', '--timeout=120s',
            ], env=environment(config), timeout=150)
        except LifecycleError:
            clean = False
        try:
            remaining = output([
                'kubectl', '-n', 'dev', 'get', kind, name,
                '--ignore-not-found', '-o', 'name',
            ], env=environment(config))
            if remaining:
                clean = False
        except LifecycleError:
            clean = False
    return clean


def preflight_database_restore(config, manifest_path=None):
    """Validate recovery inputs without opening a database network path.

    The approved private transport is created by ``restore_database`` only
    after account-bound authorization. Target emptiness remains mandatory and
    is checked through that transport immediately before ``pg_restore``.
    """
    account_guard(config)
    from lifecycle import (
        discover_latest_restore_test,
        validate_backup_manifest,
        verify_backup_restore_provenance,
    )

    backup = validate_backup_manifest(config, manifest_path, historical_dev=True)
    restore_test_path = discover_latest_restore_test(config)
    restore_test = json.loads(restore_test_path.read_text(encoding='utf-8'))
    verify_backup_restore_provenance(config, backup, restore_test, historical_dev=True)
    check_db_fresh(config, backup['values'], backup['manifest_data'])
    if backup['database'] != 'app':
        raise LifecycleError('Unexpected recovery database identity')

    validation_time = dt.datetime.now(dt.timezone.utc)
    backup_time = dt.datetime.fromisoformat(
        backup['manifest_data']['created_utc'].replace('Z', '+00:00')
    ).astimezone(dt.timezone.utc)
    backup_age_seconds = (validation_time - backup_time).total_seconds()
    report_dir = config.evidence_dir / (
        'native-database-preflight-'
        + dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%S%fZ-')
        + secrets.token_hex(4)
    )
    report_dir.mkdir(mode=0o700)
    report_path = report_dir / 'preflight.json'
    atomic_json_write(report_path, {
        'schema': 1,
        'created_utc': utc_now(),
        'validation_time_utc': validation_time.isoformat(),
        'original_backup_time_utc': backup_time.isoformat(),
        'backup_age_seconds': backup_age_seconds,
        'historical_dev_age_exception': 'DEV_ONLY_EXPLICIT_RECOVERY_PATH',
        'status': 'passed_pending_authorized_target_check',
        'account': config.expected_account_id,
        'region': config.aws_region,
        'cluster': config.cluster_name,
        'database': backup['database'],
        'rds_endpoint': backup['host'],
        'backup_manifest': str(backup['manifest']),
        'backup_sha256': backup['database_sha256'],
        'backup_source_revision': backup['source_revision'],
        'recovery_controller_revision': backup['recovery_controller_revision'],
        'restore_test_report': str(restore_test_path),
        'backup_identity_verified': True,
        'archive_integrity_verified': True,
        'restore_test_provenance_verified': True,
        'recreated_rds_identity_verified': True,
        'target_emptiness_verified': False,
        'target_emptiness_gate': (
            'mandatory in authorized native restore before pg_restore'
        ),
        'private_network_transport': (
            'authorized native temporary PostgreSQL client only'
        ),
        'restore_authorization_required': (
            'RESTORE-' + config.expected_account_id
        ),
    })
    print('PASS: Database recovery inputs verified read-only')
    print('PENDING: Target emptiness requires authorized native restore transport')
    print('MANIFEST:', backup['manifest'])
    print('REPORT:', report_path)
    return report_path


def restore_database(config, manifest_path):
    account_guard(config)
    if not manifest_path:
        raise LifecycleError('Recovery manifest path required')
    raw_manifest = Path(manifest_path)
    if raw_manifest.is_symlink():
        raise LifecycleError('Symlink recovery manifest refused')
    manifest_file = raw_manifest.resolve()
    if manifest_file.is_symlink() or not manifest_file.is_file():
        raise LifecycleError('Recovery manifest missing or invalid')
    evidence_boundary = Path(config.evidence_dir).resolve()
    if not manifest_file.is_relative_to(evidence_boundary):
        raise LifecycleError('Recovery manifest must reside within approved evidence directory')
    manifest=json.loads(manifest_file.read_text())
    values,host,db=database_connection(config)
    if manifest['account']!=config.expected_account_id or manifest['cluster']!=config.cluster_name or manifest['database']!=db:
        raise LifecycleError('Backup belongs to another environment')
    # Validate immutable archive and its independent restore-test provenance.
    # Do not equate the recovery controller revision with backup provenance.
    from lifecycle import (
        validate_backup_manifest, discover_latest_restore_test,
        verify_backup_restore_provenance, sha256_approved_file,
    )
    backup = validate_backup_manifest(config, manifest_file, historical_dev=True)
    restore_test = json.loads(discover_latest_restore_test(config).read_text())
    verify_backup_restore_provenance(config, backup, restore_test, historical_dev=True)
    check_db_fresh(config,values,manifest)
    key=age_identity()
    raw_encrypted = Path(manifest.get('database_backup', ''))
    if raw_encrypted.is_symlink():
        raise LifecycleError('Symlink database backup archive refused')
    encrypted = raw_encrypted.resolve()
    if encrypted.is_symlink() or not encrypted.is_file():
        raise LifecycleError('Encrypted database backup missing or invalid')
    allowed_backup_roots = (
        evidence_boundary,
        (Path.home() / '.local/share/eks-lab-backups').resolve(),
    )
    if not any(encrypted.is_relative_to(root) for root in allowed_backup_roots):
        raise LifecycleError('Encrypted database backup outside authorized storage boundaries')
    if sha256_approved_file(encrypted)!=manifest['database_sha256']:
        raise LifecycleError('Encrypted backup checksum mismatch')
    archive=call(['age','-d','-i',str(key),str(encrypted)])
    call(['pg_restore','--list'],data=archive)
    ca_bundle = _rds_ca_bundle()
    master=master_credentials(config,values)
    # Retrieve existing application credentials without printing values.
    app=json.loads(call(['aws','secretsmanager','get-secret-value','--secret-id',config.cluster_name+'/dev/app-runtime',
        '--query','SecretString','--output','text'],env=environment(config)))
    if app.get('username')!='app_runtime' or not app.get('password'):
        raise LifecycleError('Missing app_runtime credentials')
    def restore(_unused):
        # Credentials are supplied as environment variables from a short-lived
        # Kubernetes Secret; binary pg_restore stdin contains only the archive.
        suffix=secrets.token_hex(5)
        ca_name='native-pg-rds-ca-'+suffix
        secret_name='native-pg-credentials-'+suffix
        pod_name='native-pg-restore-'+suffix
        secret={'apiVersion':'v1','kind':'Secret','metadata':{'name':secret_name,'namespace':'dev'},'type':'Opaque',
            'stringData':{'MASTER_PASSWORD':master['password'],'APP_PASSWORD':app['password']}}
        checkpoint = CheckpointStore(
            config.evidence_dir / 'python-resume-checkpoint.json'
        )
        report_dir = config.evidence_dir / (
            'native-database-restore-'
            + dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%S%fZ-')
            + secrets.token_hex(4)
        )
        report_dir.mkdir(mode=0o700)
        report_path = report_dir / 'restore.json'
        report = {
            'schema': 1,
            'created_utc': utc_now(),
            'account': config.expected_account_id,
            'region': config.aws_region,
            'cluster': config.cluster_name,
            'database': db,
            'rds_endpoint': host,
            'backup_sha256': manifest['database_sha256'],
            'backup_source_revision': manifest.get('source_revision'),
            'alembic_revision': None,
            'restore_attempted': False,
            'archive_restore_completed': False,
            'restore_succeeded': False,
            'schema_and_permissions_verified': False,
            'temporary_resources_cleanup_verified': False,
            'status': 'running',
            'failure_type': None,
            'restore_failure_category': None,
            'cleanup_failure_category': None,
        }
        checkpoint.update('database', 'running', detail='Native database restore authorized')
        atomic_json_write(report_path, report)
        failure = None
        ca_creation_attempted = False
        try:
            ca_spec = {'apiVersion':'v1','kind':'ConfigMap', 'metadata':{'name':ca_name,'namespace':'dev'},
                       'data':{'global-bundle.pem':ca_bundle.decode('ascii')}}
            ca_creation_attempted = True
            call(['kubectl','-n','dev','create','-f','-'],data=json.dumps(ca_spec).encode(),env=environment(config))
            call(['kubectl','-n','dev','create','-f','-'],data=json.dumps(secret).encode(),env=environment(config))
            spec=client_pod(config,pod_name)
            spec['spec'].setdefault('volumes', []).append({'name':'rds-ca','configMap':{'name':ca_name,'defaultMode':292}})
            spec['spec']['containers'][0].setdefault('volumeMounts', []).append({'name':'rds-ca','mountPath':'/etc/ssl/rds','readOnly':True})
            spec['spec']['containers'][0].setdefault('env', []).extend([{'name':'PGSSLROOTCERT','value':RDS_CA_CONTAINER_PATH}] + [
                {'name':k,'valueFrom':{'secretKeyRef':{'name':secret_name,'key':k}}}
                for k in ('MASTER_PASSWORD','APP_PASSWORD')])
            call(['kubectl','-n','dev','create','-f','-'],data=json.dumps(spec).encode(),env=environment(config))
            call(['kubectl','-n','dev','wait','--for=condition=Ready','pod/'+pod_name,'--timeout=180s'],env=environment(config),timeout=220)
            def exec_pg(script, data=None, timeout=180):
                return call(['kubectl','-n','dev','exec','-i',pod_name,'--','sh','-c',script,'restore',host,master['username'],db],
                    data=data,env=environment(config),timeout=timeout)
            # No database mutation until empty-target check succeeds.
            script=_verified_pg_env('MASTER_PASSWORD')+'exec psql -X -At -v ON_ERROR_STOP=1 -h "$1" -U "$2" -d "$3" -c '+__import__('shlex').quote(EMPTY_TARGET_QUERY)
            if exec_pg(script).decode().strip()!='0':
                raise LifecycleError('Target database is not empty; refusing non-idempotent restore')
            restore_script=_verified_pg_env('MASTER_PASSWORD')+'exec pg_restore --exit-on-error --no-owner --no-acl -h "$1" -U "$2" -d "$3"'
            report['restore_attempted'] = True
            atomic_json_write(report_path, report)
            exec_pg(restore_script,data=archive,timeout=1800)
            report['archive_restore_completed'] = True
            atomic_json_write(report_path, report)
            # psql variable interpolation safely quotes password as a SQL literal.
            # Role creation is idempotent; database schema must have been empty.
            sql=("DO $role$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='app_runtime') THEN CREATE ROLE app_runtime LOGIN; END IF; END $role$;\n"
                "\\getenv pwd APP_PASSWORD\n"
                "ALTER ROLE app_runtime LOGIN PASSWORD :'pwd';\n"
                'GRANT CONNECT ON DATABASE "'+db+'" TO app_runtime;\n'
                'REVOKE CREATE ON SCHEMA public FROM PUBLIC;\n'
                'GRANT USAGE ON SCHEMA public TO app_runtime;\n'
                'GRANT SELECT,INSERT,UPDATE,DELETE ON ALL TABLES IN SCHEMA public TO app_runtime;\n'
                'GRANT USAGE,SELECT ON ALL SEQUENCES IN SCHEMA public TO app_runtime;\n')
            role_script=_verified_pg_env('MASTER_PASSWORD')+'exec psql -X -v ON_ERROR_STOP=1 -h "$1" -U "$2" -d "$3"'
            exec_pg(role_script,data=sql.encode())
            _validate_restored_database(exec_pg)
            report['restore_succeeded'] = True
            report['schema_and_permissions_verified'] = True
            report['alembic_revision'] = EXPECTED_ALEMBIC_REVISION
        except BaseException as exc:
            failure = exc
            report['failure_type'] = type(exc).__name__
        finally:
            cleanup = _cleanup_restore_resources(config, pod_name, secret_name)
            if ca_creation_attempted:
                try:
                    call(['kubectl','-n','dev','delete','configmap',ca_name,'--ignore-not-found=true','--wait=true','--timeout=120s'],env=environment(config),timeout=150)
                    if output(['kubectl','-n','dev','get','configmap',ca_name,'--ignore-not-found','-o','name'],env=environment(config)):
                        cleanup = False
                except (LifecycleError, OSError):
                    cleanup = False
            report['temporary_resources_cleanup_verified'] = cleanup
            restore_category, cleanup_category = _restore_failure_categories(
                failure,
                restore_attempted=report['restore_attempted'],
                archive_restore_completed=report['archive_restore_completed'],
                cleanup_verified=cleanup,
            )
            report['restore_failure_category'] = restore_category
            report['cleanup_failure_category'] = cleanup_category
            passed = bool(
                report['restore_succeeded']
                and report['schema_and_permissions_verified']
                and cleanup
            )
            report['status'] = 'passed' if passed else 'failed'
            atomic_json_write(report_path, report)
            checkpoint.update(
                'database', 'passed' if passed else 'failed',
                detail=(
                    'Alembic, schema, permissions and cleanup verified'
                    if passed else 'Inspect sanitized database restore evidence'
                ),
            )
        if not report['temporary_resources_cleanup_verified']:
            if failure is not None:
                raise LifecycleError(
                    'Database restore failed and temporary-resource cleanup is incomplete'
                ) from failure
            raise LifecycleError(
                'Database restore temporary-resource cleanup is incomplete'
            )
        if failure is not None:
            raise failure
        print('PASS: PostgreSQL restored; Alembic, schema, permissions and cleanup verified')
        print('REPORT:', report_path)
    return restore(None)

def platform(config):
    account_guard(config)
    script=ROOT/'scripts/lifecycle/recover_platform.py'
    if not script.is_file(): raise LifecycleError('Python platform recovery controller missing')
    call([sys.executable,str(script)],env=environment(config),timeout=3600)

def live_ready(config):
    values,_,_=database_connection(config)
    if output(['aws','eks','describe-cluster','--name',config.cluster_name,'--query','cluster.status','--output','text'],env=environment(config))!='ACTIVE':
        return False
    if output(['aws','rds','describe-db-instances','--db-instance-identifier',config.cluster_name+'-postgres',
        '--query','DBInstances[0].DBInstanceStatus','--output','text'],env=environment(config))!='available':
        return False
    return True
