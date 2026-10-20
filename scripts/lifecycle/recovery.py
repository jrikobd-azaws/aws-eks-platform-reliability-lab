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
        releases = json.loads(self.capture([
            "helm", "list", "-A", "-o", "json",
        ]))

        deployed = {
            (release["name"], release["namespace"])
            for release in releases
            if release.get("status") == "deployed"
        }

        required = {
            ("argocd", "argocd"),
            ("external-secrets", "platform-system"),
            ("aws-load-balancer-controller", "kube-system"),
        }

        if not required.issubset(deployed):
            return False

        for name in ("rds-credentials", "fastapi-auth"):
            secret = json.loads(self.capture([
                "kubectl", "-n", "dev",
                "get", "externalsecret", name,
                "-o", "json",
            ]))

            ready = any(
                c.get("type") == "Ready"
                and c.get("status") == "True"
                for c in secret.get("status", {}).get(
                    "conditions", []
                )
            )

            if not ready:
                return False

        return True

    def database_ready(self):
        """Validate previous live DB acceptance evidence and identity.

        This is NOT equivalent to a new SQL connection test.
        """
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

    def verification_ready(self):
        report = (
            self.config.evidence_dir
            / "frontend-routing-acceptance.json"
        )

        if not report.is_file():
            return False

        data = json.loads(report.read_text())

        return (
            data.get("status") == "passed"
            and data.get("results", {}).get("api_proxy")
            == "passed"
        )

    def validate(self, stage):
        checks = {
            "infrastructure": self.infrastructure_ready,
            "platform": self.platform_ready,
            "database": self.database_ready,
            "verification": self.verification_ready,
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
        """Run guarded legacy restore only against a fresh RDS.

        Never retry blindly after a partial database restore.
        """
        script = ROOT / "scripts/lifecycle/lab-resume.sh"

        if not script.is_file():
            raise LifecycleError("Database recovery script missing")

        env = self.config.environment()
        env["LAB_DATABASE_RESTORE_APPROVED"] = "YES"

        subprocess.run(
            ["bash", str(script), "restore-database"],
            cwd=ROOT,
            env=env,
            check=True,
            timeout=1800,
        )

    def verify_environment(self):
        script = ROOT / "scripts/lifecycle/labctl.py"

        self.command([
            "python3", script, "verify",
        ], timeout=1200)

    def run_stage(
        self,
        stage: str,
        action: Callable[[], None],
        *,
        execute: bool,
    ):
        if stage not in STAGES:
            raise LifecycleError("Unknown recovery stage")

        # Revalidate the live state even when checkpoint says passed.
        try:
            already_ready = self.validate(stage)
        except (
            LifecycleError,
            subprocess.CalledProcessError,
            json.JSONDecodeError,
            KeyError,
        ):
            already_ready = False

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

            if not self.validate(stage):
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

        actions = {
            "infrastructure": self.restore_infrastructure,
            "platform": self.restore_platform,
            "database": self.restore_database,
            "verification": self.verify_environment,
        }

        results = {}

        for stage in STAGES:
            if not execute:
                try:
                    ready = self.validate(stage)
                except Exception:
                    ready = False

                results[stage] = (
                    "ready" if ready else "requires_recovery"
                )
                print(f"{stage}: {results[stage]}")
                continue

            # Never proceed if the preceding stage remains unready.
            index = STAGES.index(stage)

            for prerequisite in STAGES[:index]:
                if not self.validate(prerequisite):
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
                "Database readiness uses historical acceptance evidence; "
                "fresh SQL validation is required before treating a "
                "newly recreated database as restored."
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

def restore_database(config, manifest_path):
    account_guard(config)
    manifest=json.loads(Path(manifest_path).read_text())
    values,host,db=database_connection(config)
    if manifest['account']!=config.expected_account_id or manifest['cluster']!=config.cluster_name or manifest['database']!=db:
        raise LifecycleError('Backup belongs to another environment')
    check_db_fresh(config,values,manifest)
    key=age_identity(); encrypted=Path(manifest['database_backup'])
    if not encrypted.is_file() or sha256_file(encrypted)!=manifest['database_sha256']:
        raise LifecycleError('Encrypted backup checksum mismatch')
    archive=call(['age','-d','-i',str(key),str(encrypted)])
    call(['pg_restore','--list'],data=archive)
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
        secret_name='native-pg-credentials-'+suffix
        pod_name='native-pg-restore-'+suffix
        secret={'apiVersion':'v1','kind':'Secret','metadata':{'name':secret_name,'namespace':'dev'},'type':'Opaque',
            'stringData':{'MASTER_PASSWORD':master['password'],'APP_PASSWORD':app['password']}}
        created_secret=False; created_pod=False
        try:
            call(['kubectl','-n','dev','create','-f','-'],data=json.dumps(secret).encode(),env=environment(config))
            created_secret=True
            spec=client_pod(config,pod_name)
            spec['spec']['containers'][0]['env']=[
                {'name':k,'valueFrom':{'secretKeyRef':{'name':secret_name,'key':k}}}
                for k in ('MASTER_PASSWORD','APP_PASSWORD')]
            call(['kubectl','-n','dev','create','-f','-'],data=json.dumps(spec).encode(),env=environment(config))
            created_pod=True
            call(['kubectl','-n','dev','wait','--for=condition=Ready','pod/'+pod_name,'--timeout=180s'],env=environment(config),timeout=220)
            def exec_pg(script, data=None, timeout=180):
                return call(['kubectl','-n','dev','exec','-i',pod_name,'--','sh','-c',script,'restore',host,master['username'],db],
                    data=data,env=environment(config),timeout=timeout)
            # No database mutation until empty-target check succeeds.
            query="SELECT COUNT(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname NOT IN ('pg_catalog','information_schema') AND n.nspname NOT LIKE 'pg_toast%' AND n.nspname NOT LIKE 'pg_temp_%' AND c.relkind IN ('r','p','v','m','S','f');"
            script='export PGPASSWORD="$MASTER_PASSWORD" PGSSLMODE=require; exec psql -X -At -v ON_ERROR_STOP=1 -h "$1" -U "$2" -d "$3" -c '+__import__('shlex').quote(query)
            if exec_pg(script).decode().strip()!='0':
                raise LifecycleError('Target database is not empty; refusing non-idempotent restore')
            restore_script='export PGPASSWORD="$MASTER_PASSWORD" PGSSLMODE=require; exec pg_restore --exit-on-error --no-owner --no-acl -h "$1" -U "$2" -d "$3"'
            exec_pg(restore_script,data=archive,timeout=1800)
            # psql variable interpolation safely quotes password as a SQL literal.
            # Role creation is idempotent; database schema must have been empty.
            sql=("DO $role$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='app_runtime') THEN CREATE ROLE app_runtime LOGIN; END IF; END $role$;\n"
                "ALTER ROLE app_runtime LOGIN PASSWORD :'pwd';\n"
                'GRANT CONNECT ON DATABASE "'+db+'" TO app_runtime;\n'
                'REVOKE CREATE ON SCHEMA public FROM PUBLIC;\n'
                'GRANT USAGE ON SCHEMA public TO app_runtime;\n'
                'GRANT SELECT,INSERT,UPDATE,DELETE ON ALL TABLES IN SCHEMA public TO app_runtime;\n'
                'GRANT USAGE,SELECT ON ALL SEQUENCES IN SCHEMA public TO app_runtime;\n')
            role_script='export PGPASSWORD="$MASTER_PASSWORD" PGSSLMODE=require; exec psql -X -v ON_ERROR_STOP=1 -v "pwd=$APP_PASSWORD" -h "$1" -U "$2" -d "$3"'
            exec_pg(role_script,data=sql.encode())
            # Verify real application authentication, not a historical report.
            check='SELECT current_user,has_schema_privilege(current_user,\'public\',\'USAGE\'),has_schema_privilege(current_user,\'public\',\'CREATE\');'
            verify_script='export PGPASSWORD="$APP_PASSWORD" PGSSLMODE=require; exec psql -X -At -v ON_ERROR_STOP=1 -h "$1" -U app_runtime -d "$3" -c '+__import__('shlex').quote(check)
            if exec_pg(verify_script).decode().strip()!='app_runtime|t|f':
                raise LifecycleError('Application role login/grant verification failed')
            print('PASS: PostgreSQL restored, app_runtime authenticated and privileges verified')
        finally:
            if created_pod:
                try: call(['kubectl','-n','dev','delete','pod',pod_name,'--wait=true','--timeout=120s'],env=environment(config),timeout=150)
                except LifecycleError: print('WARNING: Temporary restore pod cleanup failed',file=sys.stderr)
            if created_secret:
                try: call(['kubectl','-n','dev','delete','secret',secret_name],env=environment(config))
                except LifecycleError: print('WARNING: Temporary credential cleanup failed; delete immediately',file=sys.stderr)
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
