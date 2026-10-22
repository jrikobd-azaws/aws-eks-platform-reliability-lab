"""AWS EKS lab backup and selective shutdown operations."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
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


ALLOWED_FILE_BOUNDARIES = (
    ROOT.resolve(),
    (Path.home() / ".local/share/eks-lab-backups").resolve(),
    Path(tempfile.gettempdir()).resolve(),
)


def validate_path_boundary(path: Path, allowed_roots: tuple[Path, ...] = ALLOWED_FILE_BOUNDARIES, description: str = "file") -> Path:
    if not path:
        raise LifecycleError(f"{description} path required")
    original = Path(path)
    if original.is_symlink():
        raise LifecycleError(f"Symlink {description} refused")
    resolved = original.resolve()
    if resolved.is_symlink():
        raise LifecycleError(f"Symlink {description} refused")
    if not resolved.is_file():
        raise LifecycleError(f"{description} does not exist or is not a regular file")
    if not any(resolved.is_relative_to(root) for root in allowed_roots):
        raise LifecycleError(f"{description} is outside authorized path boundaries")
    return resolved


def sha256_file(path: Path) -> str:
    validated_path = validate_path_boundary(Path(path), description="Target file")
    digest = hashlib.sha256()

    with validated_path.open("rb") as stream:
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
        "database_cleanup_verified",
        "temporary_resources_cleanup_verified",
        "source_identity_verified",
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
        or verified.get("database_cleanup_verified") is not True
        or verified.get("temporary_resources_cleanup_verified") is not True
        or verified.get("source_identity_verified") is not True
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
ALLOWED_COMMAND_BINARIES = frozenset({
    "aws",
    "terraform",
    "kubectl",
    "helm",
    "argocd",
    "pg_restore",
    "age",
    "age-keygen",
    "python",
    "python3",
    "git",
    Path(sys.executable).name,
})


def validate_command_argv(argv):
    if not argv:
        raise LifecycleError("Empty command")
    prog = str(argv[0])
    prog_path = Path(prog)
    if prog_path.name not in ALLOWED_COMMAND_BINARIES:
        raise LifecycleError(f"Unauthorized command binary: {prog_path.name}")
    if prog_path.parent != Path("."):
        if prog_path.resolve() != Path(sys.executable).resolve():
            raise LifecycleError("Unauthorized executable path")
    validated = []
    for arg in argv:
        s = str(arg)
        if "\x00" in s or "\r" in s:
            raise LifecycleError("Command argument contains illegal control characters")
        validated.append(s)
    return validated


def _classify_subprocess_error(exc):
    """Classify subprocess failures into actionable categories."""
    if exc.returncode == 20 and str(exc.cmd[0]).endswith("argocd"):
        return "ARGOCD_SERVER_SELECTION"
    if exc.returncode in (1, 20):
        return "ARGOCD_SERVER_SELECTION" if str(exc.cmd[0]).endswith("argocd") else "UNCLASSIFIED"
    return "UNCLASSIFIED"


def call(argv, *, data=None, timeout=1200, env=None):
    """Binary-safe subprocess call; never display stderr, which may contain secrets."""
    checked_argv = validate_command_argv(argv)
    try:
        return subprocess.run(checked_argv, cwd=ROOT, env=env,
            input=data, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            timeout=timeout, check=True).stdout
    except subprocess.TimeoutExpired:
        raise LifecycleError(
            f'Command timed out: {argv[0]} after {timeout}s '
            '(private stderr suppressed)'
        ) from None
    except subprocess.CalledProcessError as exc:
        category = _classify_subprocess_error(exc)
        raise LifecycleError(
            f'Command failed: {argv[0]} (exit={exc.returncode}; '
            f'category={category}; private stderr suppressed)'
        ) from None

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
        'region':config.aws_region,'cluster':config.cluster_name,'tfvars_sha256':sha256_approved_file(var),
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
    if not report:
        raise LifecycleError('Plan report path required')
    raw_path = Path(report)
    if raw_path.is_symlink():
        raise LifecycleError('Symlink plan report refused')
    path = raw_path.resolve()
    if path.is_symlink():
        raise LifecycleError('Symlink plan report refused')
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
    if sha256_approved_file(var)!=info['tfvars_sha256']:
        raise LifecycleError('Terraform inputs changed since plan')
    raw_plan = Path(info.get('plan', ''))
    if raw_plan.is_symlink():
        raise LifecycleError('Symlink saved plan refused')
    saved = raw_plan.resolve()
    if saved.is_symlink() or not saved.is_file():
        raise LifecycleError('Saved plan missing or invalid')
    if not saved.is_relative_to(BASE.resolve()):
        raise LifecycleError('Saved plan must exist under private lifecycle evidence')
    if sha256_file(saved)!=info['plan_sha256']:
        raise LifecycleError('Saved plan checksum mismatch')
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
    source_revision=current_source_revision()
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
        'source_archive':str(source),'source_sha256':sha256_file(source),
        'source_revision':source_revision,'restore_tested':False}
    atomic_json_write(dest/'recovery-manifest.json',report)
    print('PASS: Encrypted backup and source checkpoint created and verified')
    print('RECOVERY_MANIFEST='+str(dest/'recovery-manifest.json'))
    return dest/'recovery-manifest.json'


CERTIFICATION_MAX_AGE_SECONDS = 86400
RESTORE_DATABASE_PREFIX = "restore_test_"


def _require_tools(*names):
    missing = [name for name in names if shutil.which(name) is None]
    if missing:
        raise LifecycleError("Missing required tools: " + ", ".join(missing))


def _private_evidence_file(config, path, description):
    raw = Path(path)
    if raw.is_symlink():
        raise LifecycleError(f"Symlink {description} refused")
    resolved = raw.resolve()
    boundary = Path(config.evidence_dir).resolve()
    if (
        resolved.is_symlink()
        or not resolved.is_file()
        or not resolved.is_relative_to(boundary)
    ):
        raise LifecycleError(
            f"{description} must be a regular file under private lifecycle evidence"
        )
    return resolved


def _fresh_certification_dir(config, prefix):
    base = Path(config.evidence_dir)
    base.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = base / (
        prefix + "-" + dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    )
    path.mkdir(mode=0o700)
    return path


def current_source_revision():
    revision = output(["git", "rev-parse", "HEAD"])
    if not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise LifecycleError("Invalid source revision")
    if output(["git", "status", "--porcelain"]):
        raise LifecycleError("Source tree must be clean before certification")
    return revision


def discover_latest_backup_manifest(config):
    candidates = sorted(
        Path(config.evidence_dir).glob("native-backup-*/recovery-manifest.json"),
        key=lambda item: item.stat().st_mtime,
    )
    if not candidates:
        raise LifecycleError("No approved native backup manifest found")
    return _private_evidence_file(config, candidates[-1], "backup manifest")



def sha256_approved_file(path: Path) -> str:
    """Hash a file already validated against explicit recovery boundaries."""
    path = Path(path)
    if not path.is_file() or path.is_symlink():
        raise LifecycleError("Approved archive file unavailable")
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validate_backup_manifest(config, manifest_path=None):
    _require_tools("age", "pg_restore", "git")
    latest = discover_latest_backup_manifest(config)
    manifest = (
        latest
        if manifest_path is None
        else _private_evidence_file(config, manifest_path, "backup manifest")
    )
    if manifest != latest:
        raise LifecycleError("Selected backup is not the current approved backup")
    data = json.loads(manifest.read_text())
    values, host, database = database_connection(config)
    source_revision = current_source_revision()
    if (
        data.get("account") != config.expected_account_id
        or data.get("cluster") != config.cluster_name
        or data.get("database") != database
        or data.get("rds_endpoint") != host
        or not re.fullmatch(r"[0-9a-f]{40}", str(data.get("source_revision", "")))
    ):
        raise LifecycleError("Backup identity does not match the current environment and source")
    # Historical secret identity is immutable backup provenance. The current
    # destination must instead be bound to the live recreated RDS instance.
    historical_secret = data.get("rds_master_secret_arn")
    current_secret = values.get("rds_master_secret_arn")
    if not isinstance(historical_secret, str) or not historical_secret:
        raise LifecycleError("Historical backup master-secret identity missing")
    if not isinstance(current_secret, str) or not current_secret:
        raise LifecycleError("Current RDS master-secret identity missing")
    rds = json.loads(output([
        "aws", "rds", "describe-db-instances", "--db-instance-identifier",
        config.cluster_name + "-postgres", "--output", "json",
    ], env=environment(config)))
    instances = rds.get("DBInstances", [])
    if len(instances) != 1:
        raise LifecycleError("Current RDS instance identity ambiguous")
    instance = instances[0]
    if (instance.get("DBInstanceStatus") != "available"
            or instance.get("Endpoint", {}).get("Address") != host
            or instance.get("MasterUserSecret", {}).get("SecretArn") != current_secret):
        raise LifecycleError("Current RDS master-secret ownership mismatch")
    # A replaced secret is accepted only with a matching successful historical
    # restore test; do not silently reinterpret an old backup as a new one.
    if historical_secret != current_secret:
        restore_path = discover_latest_restore_test(config)
        restore = json.loads(restore_path.read_text())
        if not (
            restore.get("status") == "passed"
            and restore.get("source_identity_verified") is True
            and restore.get("restore_tested_to_database") is True
            and restore.get("schema_verified") is True
            and restore.get("runtime_permissions_verified") is True
            and restore.get("database_cleanup_verified") is True
            and restore.get("temporary_resources_cleanup_verified") is True
            and restore.get("source_revision") == data["source_revision"]
            and restore.get("database_sha256") == data.get("database_sha256")
            and restore.get("account") == config.expected_account_id
            and restore.get("cluster") == config.cluster_name
            and restore.get("database") == database
        ):
            raise LifecycleError("Historical backup restore-test provenance mismatch")
        _verify_backup_restore_order(data, restore)
    # Historical recovery evidence may belong to an accepted
    # certification worktree rather than the current checkout.
    historical_roots = (
        config.evidence_dir.resolve(),
        (Path.home() / ".local/share/eks-lab-backups").resolve(),
    )
    backup = validate_path_boundary(
        Path(data.get("database_backup", "")),
        allowed_roots=historical_roots,
        description="Encrypted database backup",
    )
    source = validate_path_boundary(
        Path(data.get("source_archive", "")),
        allowed_roots=historical_roots,
        description="Source checkpoint",
    )
    if sha256_approved_file(backup) != data.get("database_sha256"):
        raise LifecycleError("Encrypted backup checksum mismatch")
    if sha256_approved_file(source) != data.get("source_sha256"):
        raise LifecycleError("Source checkpoint checksum mismatch")
    key = age_identity()
    archive = call(["age", "-d", "-i", str(key), str(backup)])
    if not archive.startswith(b"PGDMP"):
        raise LifecycleError("Decrypted backup is not a PostgreSQL custom archive")
    call(["pg_restore", "--list"], data=archive)
    return {
        "manifest": manifest,
        "manifest_data": data,
        "backup": backup,
        "database_sha256": data["database_sha256"],
        "source_revision": data["source_revision"],
        "recovery_controller_revision": source_revision,
        "archive": archive,
        "values": values,
        "host": host,
        "database": database,
    }


def _application_runtime_credentials(config):
    raw = call([
        "aws", "secretsmanager", "get-secret-value",
        "--secret-id", config.cluster_name + "/dev/app-runtime",
        "--query", "SecretString", "--output", "text",
    ], env=environment(config))
    value = json.loads(raw)
    if value.get("username") != "app_runtime" or not value.get("password"):
        raise LifecycleError("Approved app_runtime credentials are unavailable")
    return value


def _restore_archive_to_disposable_database(config, backup):
    suffix = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%d%H%M%S") + "_" + secrets.token_hex(4)
    database = RESTORE_DATABASE_PREFIX + suffix
    if not re.fullmatch(r"restore_test_[0-9]{14}_[0-9a-f]{8}", database):
        raise LifecycleError("Invalid disposable restore-test database name")
    pod_name = "restore-test-pg-" + secrets.token_hex(5)
    secret_name = "restore-test-credentials-" + secrets.token_hex(5)
    values = backup["values"]
    host = backup["host"]
    master = master_credentials(config, values)
    runtime = _application_runtime_credentials(config)
    result = {
        "disposable_database": database,
        "restore_succeeded": False,
        "schema_verified": False,
        "runtime_permissions_verified": False,
        "database_cleanup_verified": False,
        "temporary_resources_cleanup_verified": False,
        "failure_type": None,
    }
    secret_created = False
    pod_created = False
    database_creation_attempted = False

    def kube_absent(kind, name):
        try:
            return not output([
                "kubectl", "-n", "dev", "get", kind, name,
                "--ignore-not-found", "-o", "name",
            ], env=environment(config))
        except LifecycleError:
            return False

    def master_psql(sql, target="postgres"):
        script = (
            'export PGPASSWORD="$MASTER_PASSWORD" PGSSLMODE=require; '
            'exec psql -X -At -v ON_ERROR_STOP=1 -v "db=$4" '
            '-h "$1" -U "$2" -d "$3"'
        )
        return output([
            "kubectl", "-n", "dev", "exec", "-i", pod_name, "--",
            "sh", "-c", script, "restore-test", host,
            master["username"], target, database,
        ], data=sql.encode(), env=environment(config), timeout=300)

    try:
        secret = {
            "apiVersion": "v1", "kind": "Secret",
            "metadata": {"name": secret_name, "namespace": "dev"},
            "type": "Opaque",
            "stringData": {
                "MASTER_PASSWORD": master["password"],
                "APP_PASSWORD": runtime["password"],
            },
        }
        call(["kubectl", "-n", "dev", "create", "-f", "-"],
             data=json.dumps(secret).encode(), env=environment(config))
        secret_created = True
        pod = client_pod(config, pod_name)
        pod["spec"]["containers"][0]["env"] = [
            {
                "name": key,
                "valueFrom": {"secretKeyRef": {"name": secret_name, "key": key}},
            }
            for key in ("MASTER_PASSWORD", "APP_PASSWORD")
        ]
        call(["kubectl", "-n", "dev", "create", "-f", "-"],
             data=json.dumps(pod).encode(), env=environment(config))
        pod_created = True
        call([
            "kubectl", "-n", "dev", "wait", "--for=condition=Ready",
            "pod/" + pod_name, "--timeout=180s",
        ], env=environment(config), timeout=220)
        if master_psql("SELECT count(*) FROM pg_database WHERE datname=:'db';") != "0":
            raise LifecycleError("Disposable restore-test database already exists")
        database_creation_attempted = True
        master_psql("SELECT format('CREATE DATABASE %I', :'db') \\gexec\n")
        restore_script = (
            'export PGPASSWORD="$MASTER_PASSWORD" PGSSLMODE=require; '
            'exec pg_restore --exit-on-error --no-owner --no-acl '
            '-h "$1" -U "$2" -d "$3"'
        )
        call([
            "kubectl", "-n", "dev", "exec", "-i", pod_name, "--",
            "sh", "-c", restore_script, "restore-test", host,
            master["username"], database,
        ], data=backup["archive"], env=environment(config), timeout=1800)
        grants = (
            "REVOKE CREATE ON SCHEMA public FROM PUBLIC;\n"
            "GRANT CONNECT ON DATABASE :\"db\" TO app_runtime;\n"
            "GRANT USAGE ON SCHEMA public TO app_runtime;\n"
            "GRANT SELECT,INSERT,UPDATE,DELETE ON ALL TABLES IN SCHEMA public TO app_runtime;\n"
            "GRANT USAGE,SELECT ON ALL SEQUENCES IN SCHEMA public TO app_runtime;\n"
        )
        master_psql(grants, target=database)
        schema_query = (
            "SELECT (SELECT version_num='fe56fa70289e' FROM alembic_version),"
            "to_regclass('public.user') IS NOT NULL,"
            "to_regclass('public.item') IS NOT NULL,"
            "(SELECT data_type='uuid' FROM information_schema.columns "
            "WHERE table_schema='public' AND table_name='user' AND column_name='id'),"
            "(SELECT data_type='uuid' FROM information_schema.columns "
            "WHERE table_schema='public' AND table_name='item' AND column_name='id');"
        )
        if master_psql(schema_query, target=database) != "t|t|t|t|t":
            raise LifecycleError("Restored Alembic or UUID schema validation failed")
        result["schema_verified"] = True
        runtime_script = (
            'export PGPASSWORD="$APP_PASSWORD" PGSSLMODE=require; '
            'exec psql -X -At -v ON_ERROR_STOP=1 -h "$1" '
            '-U app_runtime -d "$2" -c "$3"'
        )
        dml_checks = [
            "has_table_privilege(current_user, 'public.\"%s\"', '%s')"
            % (table, privilege)
            for table in ("user", "item")
            for privilege in ("SELECT", "INSERT", "UPDATE", "DELETE")
        ]
        permission_query = (
            "SELECT current_user='app_runtime',"
            "has_schema_privilege(current_user,'public','USAGE'),"
            "has_schema_privilege(current_user,'public','CREATE'),"
            + ",".join(dml_checks) + ";"
        )
        permission = output([
            "kubectl", "-n", "dev", "exec", pod_name, "--", "sh", "-c",
            runtime_script, "restore-test", host, database, permission_query,
        ], env=environment(config), timeout=300)
        if permission != "|".join(("t", "t", "f") + ("t",) * 8):
            raise LifecycleError("Restored app_runtime permission validation failed")
        result["runtime_permissions_verified"] = True
        result["restore_succeeded"] = True
    except Exception as exc:
        result["failure_type"] = type(exc).__name__
    finally:
        if database_creation_attempted and pod_created:
            try:
                cleanup_sql = (
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    "WHERE datname=:'db' AND pid<>pg_backend_pid();\n"
                    "SELECT format('DROP DATABASE %I', :'db') \\gexec\n"
                )
                master_psql(cleanup_sql)
                result["database_cleanup_verified"] = (
                    master_psql("SELECT count(*) FROM pg_database WHERE datname=:'db';")
                    == "0"
                )
            except Exception:
                result["database_cleanup_verified"] = False
        elif not database_creation_attempted:
            result["database_cleanup_verified"] = True
        if pod_created:
            try:
                call([
                    "kubectl", "-n", "dev", "delete", "pod", pod_name,
                    "--wait=true", "--timeout=120s",
                ], env=environment(config), timeout=150)
            except LifecycleError:
                pass
        if secret_created:
            try:
                call([
                    "kubectl", "-n", "dev", "delete", "secret", secret_name,
                    "--wait=true", "--timeout=120s",
                ], env=environment(config), timeout=150)
            except LifecycleError:
                pass
        result["temporary_resources_cleanup_verified"] = (
            kube_absent("pod", pod_name) and kube_absent("secret", secret_name)
        )
    return result



def restore_test_backup(config, *, confirmation, manifest_path=None):
    account_guard(config)
    backup = validate_backup_manifest(config, manifest_path)
    required = (
        "RESTORE-TEST-" + config.expected_account_id + "-"
        + backup["database_sha256"][:12]
    )
    if confirmation != required:
        raise LifecycleError("Explicit restore-test approval required: " + required)
    result = _restore_archive_to_disposable_database(config, backup)
    report = {
        "schema": 1,
        "created_utc": utc_now(),
        "account": config.expected_account_id,
        "region": config.aws_region,
        "cluster": config.cluster_name,
        "database": backup["database"],
        "database_backup": str(backup["backup"]),
        "database_sha256": backup["database_sha256"],
        "source_revision": backup["source_revision"],
        "source_identity_verified": True,
        "restore_tested_to_database": result["restore_succeeded"],
        "alembic_revision": "fe56fa70289e" if result["schema_verified"] else None,
        "schema_verified": result["schema_verified"],
        "runtime_permissions_verified": result["runtime_permissions_verified"],
        "database_cleanup_verified": result["database_cleanup_verified"],
        "temporary_resources_cleanup_verified": result[
            "temporary_resources_cleanup_verified"
        ],
        "disposable_database": result["disposable_database"],
        "failure_type": result["failure_type"],
        "status": "passed" if all((
            result["restore_succeeded"],
            result["schema_verified"],
            result["runtime_permissions_verified"],
            result["database_cleanup_verified"],
            result["temporary_resources_cleanup_verified"],
        )) else "failed",
    }
    destination = _fresh_certification_dir(config, "native-restore-test")
    report_path = destination / "restore-test.json"
    atomic_json_write(report_path, report)
    if report["status"] != "passed":
        raise LifecycleError(
            "Restore test or cleanup failed; inspect private restore-test evidence"
        )
    print("PASS: Backup restored and verified in an isolated disposable database")
    print("RESTORE_TEST_REPORT=" + str(report_path))
    return report_path


def discover_latest_restore_test(config):
    candidates = sorted(
        Path(config.evidence_dir).glob("native-restore-test-*/restore-test.json"),
        key=lambda item: item.stat().st_mtime,
    )
    if not candidates:
        raise LifecycleError("No successful restore-test report found")
    return _private_evidence_file(config, candidates[-1], "restore-test report")


def _fresh_certification_timestamp(value):
    try:
        created = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
        age = (dt.datetime.now(dt.timezone.utc) - created).total_seconds()
    except (AttributeError, TypeError, ValueError):
        raise LifecycleError("Invalid certification timestamp") from None
    if not 0 <= age <= CERTIFICATION_MAX_AGE_SECONDS:
        raise LifecycleError("Restore-test certification is stale")



def _shutdown_manual_sync(application):
    """Only absent or explicitly disabled automation is manual."""
    spec = application.get("spec")
    if not isinstance(spec, dict):
        return False

    policy = spec.get("syncPolicy", {})
    if not isinstance(policy, dict):
        return False

    if "automated" not in policy:
        return True

    automated = policy["automated"]

    return (
        isinstance(automated, dict)
        and automated.get("enabled") is False
    )


def _verify_shutdown_kubernetes_identity(config):
    """Fail closed unless kubectl targets the approved EKS cluster."""
    values = terraform_values(config)

    expected_arn = (
        "arn:aws:eks:"
        + config.aws_region + ":"
        + config.expected_account_id
        + ":cluster/" + config.cluster_name
    )

    if values.get("cluster_arn") != expected_arn:
        raise LifecycleError("Terraform EKS cluster ARN mismatch")

    cluster_response = json.loads(output([
        "aws", "eks", "describe-cluster",
        "--name", config.cluster_name,
        "--output", "json",
    ], env=environment(config)))

    cluster = cluster_response.get("cluster", {})

    if (
        cluster.get("arn") != expected_arn
        or cluster.get("status") != "ACTIVE"
        or not cluster.get("endpoint")
        or not cluster.get("certificateAuthority", {}).get("data")
    ):
        raise LifecycleError("Approved EKS identity unavailable")

    current_context = output([
        "kubectl", "config", "current-context",
    ], env=environment(config))

    if current_context != expected_arn:
        raise LifecycleError("Active kubectl context is not approved EKS")

    config_data = json.loads(output([
        "kubectl", "config", "view",
        "--raw", "--minify", "-o", "json",
    ], env=environment(config)))

    contexts = config_data.get("contexts", [])
    clusters = config_data.get("clusters", [])

    if len(contexts) != 1 or len(clusters) != 1:
        raise LifecycleError("Kubernetes context identity unavailable")

    selected = config_data.get("current-context")

    if (
        selected != current_context
        or contexts[0].get("name") != current_context
        or contexts[0].get("context", {}).get("cluster")
            != clusters[0].get("name")
    ):
        raise LifecycleError("Kubernetes context mapping mismatch")

    connection = clusters[0].get("cluster", {})

    if (
        connection.get("server") != cluster["endpoint"]
        or connection.get("certificate-authority-data")
            != cluster["certificateAuthority"]["data"]
    ):
        raise LifecycleError("Kubernetes API endpoint or CA mismatch")

    return True

def verify_shutdown_live_gates(config):
    """Read-only retained-resource, dependency and manual-sync validation."""
    _verify_shutdown_kubernetes_identity(config)
    values = terraform_values(config)
    applications = {}
    names = (
        "platform-root", "aws-load-balancer-controller", "external-secrets",
        "dev-secrets", "observability-crds", "observability", "stage3-dev",
    )
    for name in names:
        value = json.loads(output([
            "kubectl", "-n", "argocd", "get", "application", name,
            "-o", "json",
        ], env=environment(config)))
        destination = value.get("spec", {}).get("destination", {})
        automated = value.get("spec", {}).get("syncPolicy", {}).get("automated")
        if destination.get("server") != "https://kubernetes.default.svc":
            raise LifecycleError("Argo CD destination identity mismatch")
        if not _shutdown_manual_sync(value):
            raise LifecycleError("Argo CD automatic synchronization is enabled")
        applications[name] = value
    root_automated = applications["platform-root"].get("spec", {}).get(
        "syncPolicy", {}
    ).get("automated", {})
    if root_automated.get("prune") is not False:
        raise LifecycleError("Platform root pruning is not disabled")

    def items(args):
        return json.loads(output(args + ["-o", "json"], env=environment(config))).get(
            "items", []
        )

    dependencies = {
        "ingress": len(items(["kubectl", "get", "ingress", "-A"])),
        "load_balancer_service": sum(
            service.get("spec", {}).get("type") == "LoadBalancer"
            for service in items(["kubectl", "get", "service", "-A"])
        ),
        "pvc": len(items(["kubectl", "get", "pvc", "-A"])),
        "pv": len(items(["kubectl", "get", "pv"])),
        "target_group_binding": len(items([
            "kubectl", "get", "targetgroupbindings.elbv2.k8s.aws", "-A",
        ])),
    }
    vpc = values.get("vpc_id")
    if not vpc:
        raise LifecycleError("Terraform VPC identity missing")
    load_balancers = json.loads(output([
        "aws", "elbv2", "describe-load-balancers", "--output", "json",
    ], env=environment(config))).get("LoadBalancers", [])
    target_groups = json.loads(output([
        "aws", "elbv2", "describe-target-groups", "--output", "json",
    ], env=environment(config))).get("TargetGroups", [])
    dependencies["vpc_load_balancer"] = sum(
        item.get("VpcId") == vpc for item in load_balancers
    )
    dependencies["vpc_target_group"] = sum(
        item.get("VpcId") == vpc for item in target_groups
    )
    if any(dependencies.values()):
        raise LifecycleError("GitOps-managed storage or load-balancer dependencies remain")

    for secret_id in (
        values.get("rds_master_secret_arn"),
        config.cluster_name + "/dev/app-runtime",
    ):
        if not secret_id:
            raise LifecycleError("Retained secret identity missing")
        call([
            "aws", "secretsmanager", "describe-secret", "--secret-id", secret_id,
        ], env=environment(config))
    return {
        "retained_resources_verified": True,
        "gitops_cleanup_verified": True,
        "root_autosync_pause_verified": True,
        "dependency_counts": dependencies,
        "applications_checked": list(names),
    }





def _certification_datetime(value, description):
    try:
        created = dt.datetime.fromisoformat(
            value.replace("Z", "+00:00")
        )
        if created.tzinfo is None:
            raise ValueError("Timezone missing")
        created = created.astimezone(dt.timezone.utc)
    except (AttributeError, TypeError, ValueError):
        raise LifecycleError(
            "Invalid " + description + " timestamp"
        ) from None

    age = (
        dt.datetime.now(dt.timezone.utc) - created
    ).total_seconds()

    if not 0 <= age <= CERTIFICATION_MAX_AGE_SECONDS:
        raise LifecycleError(
            description + " timestamp is stale or in the future"
        )

    return created


def verify_backup_restore_provenance(config, backup, restore):
    """Bind historical backup provenance to a successful isolated restore test.

    The current recovery-controller revision is intentionally independent of
    the immutable source revision recorded when the archive was created.
    """
    manifest = backup["manifest_data"]
    required = (
        restore.get("status") == "passed"
        and restore.get("restore_tested_to_database") is True
        and restore.get("schema_verified") is True
        and restore.get("runtime_permissions_verified") is True
        and restore.get("database_cleanup_verified") is True
        and restore.get("temporary_resources_cleanup_verified") is True
        and restore.get("source_identity_verified") is True
        and restore.get("source_revision") == manifest.get("source_revision")
        and restore.get("database_sha256") == backup["database_sha256"]
        and restore.get("account") == config.expected_account_id
        and restore.get("cluster") == config.cluster_name
        and restore.get("database") == backup["database"]
    )
    if not required:
        raise LifecycleError("Backup and isolated restore-test provenance mismatch")
    _verify_backup_restore_order(manifest, restore)
    return True


def _verify_backup_restore_order(backup, restore):
    backup_created = _certification_datetime(
        backup.get("created_utc"), "Backup"
    )
    restore_created = _certification_datetime(
        restore.get("created_utc"), "Restore-test"
    )

    if restore_created < backup_created:
        raise LifecycleError(
            "Restore-test evidence predates selected backup"
        )

    return True

def certify_shutdown(config, *, plan_report, tfvars, confirmation):
    account_guard(config)
    source_revision = current_source_revision()
    report_path = _private_evidence_file(config, plan_report, "shutdown plan report")
    plan = json.loads(report_path.read_text())
    if (
        plan.get("kind") != "shutdown"
        or plan.get("account") != config.expected_account_id
        or plan.get("region") != config.aws_region
        or plan.get("cluster") != config.cluster_name
    ):
        raise LifecycleError("Shutdown plan identity mismatch")
    var = checked_inputs(tfvars)
    if sha256_file(var) != plan.get("tfvars_sha256"):
        raise LifecycleError("Terraform inputs changed since plan")
    saved = _private_evidence_file(config, plan.get("plan"), "saved shutdown plan")
    if sha256_file(saved) != plan.get("plan_sha256"):
        raise LifecycleError("Saved shutdown plan checksum mismatch")
    required = (
        "CERTIFY-" + config.expected_account_id + "-" + plan["plan_sha256"][:12]
    )
    if confirmation != required:
        raise LifecycleError("Explicit reviewed-plan certification required: " + required)
    state = json.loads(call([
        "terraform", f"-chdir={TF}", "state", "pull",
    ], env=environment(config)))
    if (state.get("lineage"), state.get("serial")) != (
        plan.get("state_lineage"), plan.get("state_serial"),
    ):
        raise LifecycleError("Terraform state changed since reviewed plan; replan")
    assert_plan_policy(
        plan_json(config, saved), plan.get("targets", []), action="delete"
    )
    backup = validate_backup_manifest(config)
    restore_path = discover_latest_restore_test(config)
    restore = json.loads(restore_path.read_text())
    verify_backup_restore_provenance(config, backup, restore)
    live = verify_shutdown_live_gates(config)
    readiness = {
        "schema": 1,
        "created_utc": utc_now(),
        "account": config.expected_account_id,
        "region": config.aws_region,
        "cluster": config.cluster_name,
        "source_revision": source_revision,
        "safe_to_destroy": True,
        "backup_verified": True,
        "restore_tested_to_database": True,
        "database_cleanup_verified": True,
        "temporary_resources_cleanup_verified": True,
        "source_identity_verified": True,
        "retained_resources_verified": live["retained_resources_verified"],
        "gitops_cleanup_verified": live["gitops_cleanup_verified"],
        "terraform_plan_reviewed": True,
        "root_autosync_pause_verified": live["root_autosync_pause_verified"],
        "database_backup": str(backup["backup"]),
        "database_sha256": backup["database_sha256"],
        "restore_test_report": str(restore_path),
        "restore_test_sha256": sha256_file(restore_path),
        "plan_report": str(report_path),
        "plan_sha256": plan["plan_sha256"],
        "state_lineage": plan["state_lineage"],
        "state_serial": plan["state_serial"],
        "dependency_counts": live["dependency_counts"],
    }
    readiness_path = Path(config.evidence_dir) / "shutdown-readiness.json"
    atomic_json_write(readiness_path, readiness)
    require_shutdown_readiness(config, plan)
    print("PASS: Shutdown readiness certification generated and self-verified")
    print("SHUTDOWN_READINESS=" + str(readiness_path))
    return readiness_path
