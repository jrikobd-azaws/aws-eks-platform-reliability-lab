"""AWS EKS lab backup and selective shutdown operations."""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

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


PROTECTED_TYPES = (
    "aws_ecr_repository",
    "aws_s3_bucket",
    "aws_secretsmanager_secret",
    "aws_secretsmanager_secret_version",
)

DISPOSABLE_MODULES = (
    "module.eks.",
    "module.network.",
    "module.rds.",
    "module.platform_iam.",
)


def is_data_source(address: str) -> bool:
    return address.startswith("data.") or ".data." in address


def is_protected(address: str) -> bool:
    return any(
        address.startswith(resource_type + ".")
        or f".{resource_type}." in address
        for resource_type in PROTECTED_TYPES
    )


def classify_resources(addresses: list[str]) -> list[str]:
    """Fail closed on unknown managed Terraform addresses."""
    targets = []
    unknown = []

    for address in addresses:
        if is_data_source(address):
            continue

        if is_protected(address):
            continue

        if address.startswith(DISPOSABLE_MODULES):
            targets.append(address)
        else:
            unknown.append(address)

    if unknown:
        raise LifecycleError(
            "Unclassified Terraform resources: "
            + ", ".join(sorted(unknown))
        )

    if not targets:
        raise LifecycleError("No disposable Terraform resources")

    return sorted(set(targets))


def validate_destroy_plan(
    plan: dict[str, Any],
    targets: list[str],
) -> dict[str, Any]:
    """Validate exact managed deletions, allowing data-source reads."""
    approved = set(targets)
    deleted = set()
    reads = 0

    for resource in plan.get("resource_changes", []):
        address = resource["address"]
        actions = resource["change"]["actions"]
        mode = resource.get("mode", "managed")

        if actions == ["no-op"]:
            continue

        if mode == "data" and actions == ["read"]:
            reads += 1
            continue

        if is_protected(address):
            raise LifecycleError(
                f"Protected resource targeted: {address}"
            )

        if mode != "managed" or actions != ["delete"]:
            raise LifecycleError(
                f"Unexpected Terraform action: {address}: {actions}"
            )

        if address not in approved:
            raise LifecycleError(
                f"Unapproved deletion: {address}"
            )

        deleted.add(address)

    missing = approved - deleted

    if missing:
        raise LifecycleError(
            "Targets missing from destroy plan: "
            + ", ".join(sorted(missing))
        )

    if not deleted:
        raise LifecycleError("Destroy plan has no deletions")

    return {
        "delete_count": len(deleted),
        "data_source_reads": reads,
        "protected_resources_unchanged": True,
    }


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()

    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)

    return digest.hexdigest()


@dataclass
class LifecycleManager:
    config: LabConfig
    runner: CommandRunner
    checkpoint: CheckpointStore

    def check_account(self) -> None:
        verify_account(
            self.runner,
            self.config.expected_account_id,
        )

    def backup(self) -> dict[str, Any]:
        """Run proven encrypted backup script and verify new evidence."""
        self.check_account()

        script = ROOT / "scripts/lifecycle/lab-db-backup.sh"

        if not script.is_file():
            raise LifecycleError("Existing backup script missing")

        self.checkpoint.update("backup", "running")

        try:
            self.runner.run(
                ["bash", script],
                timeout=1800,
            )

            recovery_root = (
                self.config.evidence_dir / "recovery"
            )

            candidates = sorted(
                path for path in recovery_root.glob("*")
                if path.is_dir()
                and (path / "recovery-manifest.json").exists()
            )

            if not candidates:
                raise LifecycleError("No recovery manifest found")

            recovery = candidates[-1]
            manifest = json.loads(
                (recovery / "recovery-manifest.json").read_text()
            )

            archive = Path(manifest["database_backup"])

            if not archive.is_file() or archive.stat().st_size == 0:
                raise LifecycleError("Encrypted database archive missing")

            source = recovery / "source.tar.gz"

            if not source.is_file():
                raise LifecycleError("Source checkpoint missing")

            result = {
                "timestamp_utc": utc_now(),
                "recovery_directory": str(recovery),
                "database_archive": str(archive),
                "database_sha256": sha256_file(archive),
                "source_sha256": sha256_file(source),
                "status": "passed",
            }

            atomic_json_write(
                self.config.evidence_dir / "python-backup-latest.json",
                result,
            )

            self.checkpoint.update("backup", "passed")
            return result

        except Exception:
            self.checkpoint.update("backup", "failed")
            raise

    def plan_shutdown(self) -> dict[str, Any]:
        """Generate a Terraform targeted destroy plan; never apply it."""
        self.check_account()

        tf = self.config.terraform_dir
        evidence = self.config.evidence_dir
        run = evidence / ("python-shutdown-" + utc_now().replace(":", "-"))
        run.mkdir(parents=True, exist_ok=False)

        state = self.runner.run([
            "terraform",
            f"-chdir={tf}",
            "state",
            "list",
        ])

        addresses = state.stdout.splitlines()
        targets = classify_resources(addresses)

        # Use the existing tested variable derivation until Phase 3
        # replaces the remaining Bash recovery dependencies.
        existing = ROOT / "scripts/lifecycle/stage3-selective-shutdown-plan.sh"

        if not existing.is_file():
            raise LifecycleError("Existing Terraform planner missing")

        self.runner.run(
            ["bash", existing, "--plan"],
            timeout=1800,
        )

        plans = sorted(
            evidence.glob("selective-plan-*/selective-destroy.tfplan"),
            key=lambda path: path.stat().st_mtime,
        )

        if not plans:
            raise LifecycleError("No selective Terraform plan generated")

        plan_file = plans[-1]

        result = self.runner.run([
            "terraform",
            f"-chdir={tf}",
            "show",
            "-json",
            plan_file,
        ])

        plan = json.loads(result.stdout)
        validation = validate_destroy_plan(plan, targets)

        summary = {
            "timestamp_utc": utc_now(),
            "plan": str(plan_file),
            "plan_sha256": sha256_file(plan_file),
            "account": self.config.expected_account_id,
            "targets": targets,
            "validation": validation,
            "status": "validated",
        }

        atomic_json_write(run / "shutdown-plan.json", summary)
        atomic_json_write(
            evidence / "python-shutdown-latest.json",
            summary,
        )

        self.checkpoint.update("shutdown_plan", "passed")
        return summary

    def execute_shutdown(
        self,
        *,
        confirmation: str,
    ) -> dict[str, Any]:
        """Apply only a previously validated and approved plan."""
        self.check_account()

        required = (
            "DESTROY-"
            + self.config.expected_account_id
        )

        if confirmation != required:
            raise LifecycleError(
                "Explicit account-specific destroy approval required"
            )

        summary_path = (
            self.config.evidence_dir / "python-shutdown-latest.json"
        )

        if not summary_path.is_file():
            raise LifecycleError("Validated shutdown plan missing")

        summary = json.loads(summary_path.read_text())
        plan = Path(summary["plan"])

        if not plan.is_file():
            raise LifecycleError("Saved shutdown plan missing")

        if sha256_file(plan) != summary["plan_sha256"]:
            raise LifecycleError("Saved plan checksum mismatch")

        result = self.runner.run([
            "terraform",
            f"-chdir={self.config.terraform_dir}",
            "show",
            "-json",
            plan,
        ])

        validate_destroy_plan(
            json.loads(result.stdout),
            summary["targets"],
        )

        raise LifecycleError(
            "Legacy shutdown blocked: use certified native workflow"
        )

        self.checkpoint.update("shutdown", "running")

        try:
            self.runner.run([
                "terraform",
                f"-chdir={self.config.terraform_dir}",
                "apply",
                "-input=false",
                plan,
            ], timeout=7200)

            self.checkpoint.update("shutdown", "passed")

            return {
                "status": "passed",
                "timestamp_utc": utc_now(),
                "plan": str(plan),
            }

        except Exception:
            self.checkpoint.update("shutdown", "failed")
            raise


# LIFECYCLE_RECOVERY_GUARD_V2
def require_shutdown_readiness(config, plan):
    """Fail closed until recovery is independently certified."""
    import json
    from pathlib import Path
    from datetime import datetime, timezone

    record = Path(config.evidence_dir) / "shutdown-readiness.json"

    if (
        not record.is_file()
        or record.is_symlink()
        or record.stat().st_mode & 0o077
    ):
        raise LifecycleError(
            "Shutdown blocked: private recovery certification missing"
        )

    data = json.loads(record.read_text())

    required = (
        "safe_to_destroy",
        "backup_verified",
        "restore_tested_to_database",
        "retained_resources_verified",
        "gitops_cleanup_verified",
        "terraform_plan_reviewed",
        "root_autosync_pause_verified",
    )

    if any(data.get(field) is not True for field in required):
        raise LifecycleError(
            "Shutdown blocked: recovery certification incomplete"
        )

    if (
        data.get("account") != config.expected_account_id
        or data.get("cluster") != config.cluster_name
        or data.get("plan_sha256") != plan.get("plan_sha256")
        or data.get("state_lineage") != plan.get("state_lineage")
        or data.get("state_serial") != plan.get("state_serial")
    ):
        raise LifecycleError(
            "Shutdown blocked: recovery certification mismatch"
        )

    created = data.get("created_utc")

    try:
        timestamp = datetime.fromisoformat(
            created.replace("Z", "+00:00")
        )
        age = (
            datetime.now(timezone.utc) - timestamp
        ).total_seconds()
    except (AttributeError, ValueError, TypeError):
        raise LifecycleError(
            "Shutdown blocked: invalid certification timestamp"
        )

    if not 0 <= age <= 86400:
        raise LifecycleError(
            "Shutdown blocked: certification expired"
        )

    backup = Path(data.get("database_backup", ""))
    restore = Path(data.get("restore_test_report", ""))

    if (
        not backup.is_absolute()
        or backup.is_symlink()
        or not backup.is_file()
        or not restore.is_absolute()
        or restore.is_symlink()
        or not restore.is_file()
    ):
        raise LifecycleError(
            "Shutdown blocked: backup or restore evidence unavailable"
        )

    if sha256_file(backup) != data.get("database_sha256"):
        raise LifecycleError(
            "Shutdown blocked: encrypted backup checksum mismatch"
        )

    if sha256_file(restore) != data.get("restore_test_sha256"):
        raise LifecycleError(
            "Shutdown blocked: restore evidence checksum mismatch"
        )

    verified = json.loads(restore.read_text())

    if (
        verified.get("restore_tested_to_database") is not True
        or verified.get("database_sha256") != data.get("database_sha256")
        or verified.get("account") != config.expected_account_id
        or verified.get("cluster") != config.cluster_name
    ):
        raise LifecycleError(
            "Shutdown blocked: restore-test certification invalid"
        )

    print("PASS: Recovery-readiness certification verified")


# CLI_A_NATIVE_V2
import datetime as dt
import re
import secrets
import sys
import tarfile
TF=ROOT/"infra/terraform/environments/lab"
BASE=ROOT/".private-evidence/lifecycle"
def call(argv, *, data=None, timeout=1200, env=None):
    """Binary-safe subprocess call; never display stderr, which may contain secrets."""
    try:
        return subprocess.run([str(x) for x in argv], cwd=ROOT, env=env,
            input=data, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            timeout=timeout, check=True).stdout
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        raise LifecycleError('Command failed or timed out: '+str(argv[0])+' (private stderr suppressed)') from None

def output(argv, **kwargs):
    return call(argv, **kwargs).decode().strip()

def environment(config):
    return config.environment()

def account_guard(config):
    actual=output(['aws','sts','get-caller-identity','--query','Account','--output','text'], env=environment(config))
    if actual != config.expected_account_id:
        raise LifecycleError('Wrong AWS account')
    if output(['terraform',f'-chdir={TF}','workspace','show'], env=environment(config)) != 'default':
        raise LifecycleError('Unexpected Terraform workspace')

def terraform_values(config):
    raw=json.loads(output(['terraform',f'-chdir={TF}','output','-json'],env=environment(config)))
    return {k:v['value'] for k,v in raw.items()}

def checked_inputs(path):
    if not path or not Path(path).is_file():
        raise LifecycleError('Explicit --tfvars JSON file is required')
    original=Path(path)
    if original.is_symlink(): raise LifecycleError('Symlink tfvars refused')
    p=original.resolve()
    if p.is_symlink() or p.stat().st_mode & 0o077:
        raise LifecycleError('tfvars must be a private, non-symlink file (chmod 600)')
    if not isinstance(json.loads(p.read_text()),dict):
        raise LifecycleError('tfvars JSON must be an object')
    return p

def assert_plan_policy(plan, targets, *, action):
    """Strictly separate data reads, retained no-ops and managed actions."""
    changed=[]
    for resource in plan.get('resource_changes',[]):
        address=resource['address']; actions=resource['change']['actions']; mode=resource.get('mode','managed')
        if actions == ['no-op']: continue
        if mode == 'data' and actions == ['read']: continue
        if mode != 'managed' or actions != [action] or is_protected(address):
            raise LifecycleError(f'Unsafe Terraform action: {address}: {actions}')
        changed.append(address)
    if not changed:
        raise LifecycleError('No managed changes in Terraform plan')
    if action == 'delete' and set(changed) != set(targets):
        raise LifecycleError('Destroy plan differs from exact disposable target inventory')
    if action == 'create' and any(is_data_source(x) for x in changed):
        raise LifecycleError('Data source in managed create inventory')
    if len(changed) != len(set(changed)):
        raise LifecycleError('Duplicate resource actions')
    return {'action':action,'count':len(changed),'addresses':changed}

def plan_json(config, path):
    return json.loads(call(['terraform',f'-chdir={TF}','show','-json',str(path)],env=environment(config)))

def fresh_dir(prefix):
    path=BASE/(prefix+'-'+dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%S%fZ'))
    path.mkdir(parents=True,mode=0o700)
    return path

def create_plan(config, tfvars, kind):
    account_guard(config)
    var=checked_inputs(tfvars)
    dest=fresh_dir('native-'+kind)
    args=['terraform',f'-chdir={TF}','plan','-input=false','-lock-timeout=60s','-var-file='+str(var)]
    targets=[]
    if kind=='shutdown':
        addresses=output(['terraform',f'-chdir={TF}','state','list'],env=environment(config)).splitlines()
        targets=classify_resources(addresses)
        # A targeted destroy is exceptional; never treat it as a routine full destroy.
        args+=['-destroy']+['-target='+a for a in targets]
    elif kind != 'rebuild':
        raise LifecycleError('Unknown plan kind')
    saved=dest/'terraform.tfplan'
    args+=['-out='+str(saved)]
    call(args,env=environment(config),timeout=2400)
    checked=assert_plan_policy(plan_json(config,saved),targets,action='delete' if kind=='shutdown' else 'create')
    # A rebuild must only create managed resources. Retained resources must be no-ops.
    summary={'schema':1,'kind':kind,'created_utc':utc_now(),'account':config.expected_account_id,
        'region':config.aws_region,'cluster':config.cluster_name,'tfvars_sha256':sha256_file(var),
        'plan':str(saved),'plan_sha256':sha256_file(saved),'targets':targets,'policy':checked,
        'state_lineage':None}
    state=json.loads(call(['terraform',f'-chdir={TF}','state','pull'],env=environment(config)))
    summary['state_lineage']=state.get('lineage')
    summary['state_serial']=state.get('serial')
    atomic_json_write(dest/'plan.json',summary)
    print('PASS:',kind,'plan validated:',checked['count'],'managed',checked['action'],'actions')
    print('PLAN_REPORT='+str(dest/'plan.json'))
    return dest/'plan.json'

def execute_plan(config, report, confirmation, tfvars):
    account_guard(config)
    path=Path(report).resolve()
    if not path.is_file() or not path.is_relative_to(BASE.resolve()):
        raise LifecycleError('Plan report must exist under private lifecycle evidence')
    info=json.loads(path.read_text())
    if info.get('account')!=config.expected_account_id or info.get('region')!=config.aws_region or info.get('cluster')!=config.cluster_name:
        raise LifecycleError('Plan belongs to a different environment')
    kind=info['kind'];
    if kind not in ('shutdown','rebuild'): raise LifecycleError('Invalid plan kind')
    required=f'{kind.upper()}-{config.expected_account_id}'
    if confirmation!=required:
        raise LifecycleError('Explicit confirmation required: '+required)
    var=checked_inputs(tfvars)
    if sha256_file(var)!=info['tfvars_sha256']:
        raise LifecycleError('Terraform inputs changed since plan')
    saved=Path(info['plan'])
    if not saved.is_file() or sha256_file(saved)!=info['plan_sha256']:
        raise LifecycleError('Saved plan missing or altered')
    state=json.loads(call(['terraform',f'-chdir={TF}','state','pull'],env=environment(config)))
    if (state.get('lineage'),state.get('serial')) != (info['state_lineage'],info['state_serial']):
        raise LifecycleError('Terraform state changed since plan; replan')
    assert_plan_policy(plan_json(config,saved),info['targets'],action='delete' if kind=='shutdown' else 'create')
    if kind == 'shutdown':
        require_shutdown_readiness(config, info)

    # Terraform itself checks state and plan freshness again during apply.
    call(['terraform',f'-chdir={TF}','apply','-input=false',str(saved)],env=environment(config),timeout=9000)
    print('PASS: Applied reviewed',kind,'plan')

def client_pod(config, name):
    return {'apiVersion':'v1','kind':'Pod','metadata':{'name':name,'namespace':'dev'},
      'spec':{'restartPolicy':'Never','automountServiceAccountToken':False,
        'securityContext':{'runAsNonRoot':True,'runAsUser':65534,'runAsGroup':65534,'seccompProfile':{'type':'RuntimeDefault'}},
        'containers':[{'name':'pg','image':'postgres:16.15-alpine','command':['sh','-c','sleep 1800'],
          'securityContext':{'allowPrivilegeEscalation':False,'readOnlyRootFilesystem':True,'capabilities':{'drop':['ALL']}},
          'resources':{'requests':{'cpu':'25m','memory':'32Mi'},'limits':{'cpu':'200m','memory':'256Mi'}}}]}}

def temporary_pg_pod(config, work):
    name='native-pg-'+secrets.token_hex(5)
    call(['kubectl','-n','dev','create','-f','-'],data=json.dumps(client_pod(config,name)).encode(),env=environment(config))
    try:
        call(['kubectl','-n','dev','wait','--for=condition=Ready','pod/'+name,'--timeout=180s'],env=environment(config),timeout=220)
        return work(name)
    finally:
        try:
            call(['kubectl','-n','dev','delete','pod',name,'--ignore-not-found=true','--wait=true','--timeout=120s'],env=environment(config),timeout=150)
        except LifecycleError:
            print('WARNING: Temporary PostgreSQL pod cleanup needs inspection',file=sys.stderr)

def age_identity():
    p=Path.home()/'.config/eks-lab/age-backup-key.txt'
    if not p.is_file() or p.is_symlink() or p.stat().st_mode & 0o077:
        raise LifecycleError('Missing or insecure age identity (must be chmod 600)')
    return p

def database_connection(config):
    values=terraform_values(config)
    if values['cluster_name']!=config.cluster_name: raise LifecycleError('Cluster mismatch')
    host=values['rds_address']; db=values['rds_database_name']
    if not re.fullmatch(r'[A-Za-z0-9.-]+\.rds\.amazonaws\.com',host) or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*',db):
        raise LifecycleError('Unexpected database host or name')
    return values,host,db

def master_credentials(config,values):
    raw=call(['aws','secretsmanager','get-secret-value','--secret-id',values['rds_master_secret_arn'],
        '--query','SecretString','--output','text'],env=environment(config))
    cred=json.loads(raw)
    if not all(isinstance(cred.get(k),str) and cred[k] for k in ('username','password')):
        raise LifecycleError('Missing RDS master credential fields')
    return cred

def backup(config):
    account_guard(config)
    values,host,db=database_connection(config)
    if output(['aws','rds','describe-db-instances','--db-instance-identifier',config.cluster_name+'-postgres',
        '--query','DBInstances[0].DBInstanceStatus','--output','text'],env=environment(config))!='available':
        raise LifecycleError('RDS not available')
    key=age_identity(); recipient=output(['age-keygen','-y',str(key)])
    if not recipient.startswith('age1'): raise LifecycleError('Invalid age recipient')
    cred=master_credentials(config,values)
    def dump(pod):
        # The credential is supplied through stdin, never command args.
        script='IFS= read -r PGPASSWORD; export PGPASSWORD PGSSLMODE=require; exec pg_dump -h "$1" -U "$2" -d "$3" -Fc --no-owner --no-acl'
        return call(['kubectl','-n','dev','exec','-i',pod,'--','sh','-c',script,'backup',host,cred['username'],db],
            data=(cred['password']+'\n').encode(),env=environment(config),timeout=1200)
    archive=temporary_pg_pod(config,dump)
    if not archive.startswith(b'PGDMP'): raise LifecycleError('Invalid PostgreSQL custom archive')
    call(['pg_restore','--list'],data=archive)
    encrypted=call(['age','-r',recipient],data=archive)
    decrypted=call(['age','-d','-i',str(key)],data=encrypted)
    if decrypted!=archive: raise LifecycleError('Encrypted archive verification failed')
    dest=fresh_dir('native-backup')
    file=dest/'database.dump.age'; file.write_bytes(encrypted); file.chmod(0o600)
    # Preserve source checkpoint without secrets or local runtime state.
    source=dest/'source.tar.gz'
    with tarfile.open(source,'w:gz') as t:
        for part in ('scripts/lifecycle','infra/terraform','gitops','app/helm'):
            t.add(ROOT/part,arcname=part,filter=lambda x: None if any(k in x.name for k in ('.terraform/','.private-evidence/','__pycache__/','.tfstate','.env')) else x)
    report={'created_utc':utc_now(),'account':config.expected_account_id,'cluster':config.cluster_name,
        'rds_endpoint':host,'rds_master_secret_arn':values['rds_master_secret_arn'],
        'database':db,'database_backup':str(file),'database_sha256':sha256_file(file),
        'source_archive':str(source),'source_sha256':sha256_file(source),'restore_tested':False}
    atomic_json_write(dest/'recovery-manifest.json',report)
    print('PASS: Encrypted backup and source checkpoint created and verified')
    print('RECOVERY_MANIFEST='+str(dest/'recovery-manifest.json'))
    return dest/'recovery-manifest.json'
