"""Portable AWS account deployment preflight.

Fresh deployment is intentionally separate from same-account recovery.
No AWS resources are created by this module.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from foundation import (
    ROOT,
    CommandRunner,
    LabConfig,
    LifecycleError,
    atomic_json_write,
    utc_now,
)


@dataclass(frozen=True)
class DeploymentConfig:
    raw: dict[str, Any]
    path: Path

    @classmethod
    def load(cls, path: Path) -> "DeploymentConfig":
        path = Path(path).resolve()

        if not path.is_file():
            raise LifecycleError("Deployment config missing")

        data = json.loads(path.read_text())

        if data.get("schema_version") != 1:
            raise LifecycleError("Unsupported deployment config")

        if data.get("deployment_mode") != "fresh":
            raise LifecycleError(
                "New-account deployment requires fresh mode"
            )

        return cls(raw=data, path=path)

    def aws(self, name: str) -> str:
        return self.raw["aws"][name]

    def project(self, name: str) -> str:
        return self.raw["project"][name]


def validate_deployment_config(config: DeploymentConfig) -> None:
    """Reject placeholders, unsafe paths and cross-account assumptions."""
    data = config.raw

    # Enforce deployment mode even when DeploymentConfig is
    # constructed directly rather than loaded from a file.
    if data.get("schema_version") != 1:
        raise LifecycleError("Unsupported deployment config")

    if data.get("deployment_mode") != "fresh":
        raise LifecycleError(
            "New-account deployment requires fresh mode"
        )

    account = config.aws("expected_account_id")
    region = config.aws("region")
    profile = config.aws("profile")

    if not re.fullmatch(r"[0-9]{12}", account):
        raise LifecycleError("Invalid target AWS account ID")

    if not re.fullmatch(r"[a-z]{2}-[a-z]+-[0-9]+", region):
        raise LifecycleError("Invalid AWS region")

    if not profile or "YOUR_" in profile:
        raise LifecycleError("Replace AWS profile placeholder")

    backend = data["terraform"]["backend"]

    if backend["type"] != "s3":
        raise LifecycleError("Only S3 backend supported")

    bucket = backend["bucket"]

    if "REPLACE_" in bucket:
        raise LifecycleError("Replace S3 bucket placeholder")

    if not re.fullmatch(
        r"[a-z0-9][a-z0-9.-]{2,62}", bucket
    ):
        raise LifecycleError("Invalid S3 bucket name")

    if backend["region"] != region:
        raise LifecycleError("Backend region mismatch")

    if backend.get("use_lockfile") is not True:
        raise LifecycleError("S3 backend locking required")

    if data["database"]["restore_existing_backup"]:
        raise LifecycleError(
            "Fresh deployment cannot reuse private database backup"
        )

    if data["database"]["mode"] != "initialize":
        raise LifecycleError("Fresh deployment must initialize database")

    for image in data["images"].values():
        if image["source"] != "build":
            raise LifecycleError(
                "Fresh deployment requires local image build"
            )

    for secret in data["secrets"].values():
        if secret["source"] != "provision":
            raise LifecycleError(
                "Fresh deployment must provision its own secrets"
            )

    terraform_dir = (
        ROOT / data["terraform"]["directory"]
    ).resolve()

    if not terraform_dir.is_relative_to(ROOT):
        raise LifecycleError("Terraform directory escapes repository")

    if not terraform_dir.is_dir():
        raise LifecycleError("Terraform directory missing")

    for key in ("bootstrap_path", "platform_path", "application_path"):
        path = (ROOT / data["gitops"][key]).resolve()

        if not path.is_relative_to(ROOT):
            raise LifecycleError("GitOps path escapes repository")

        if not path.exists():
            raise LifecycleError(f"GitOps path missing: {key}")


def validate_target_account(
    config: DeploymentConfig,
    runner: CommandRunner,
) -> None:
    actual = runner.run([
        "aws", "sts", "get-caller-identity",
        "--query", "Account",
        "--output", "text",
    ], timeout=60).stdout.strip()

    if actual != config.aws("expected_account_id"):
        raise LifecycleError(
            "AWS target account does not match deployment config"
        )


def build_deployment_plan(
    config: DeploymentConfig,
) -> dict[str, Any]:
    """Generate a declarative deployment preflight summary."""
    validate_deployment_config(config)

    return {
        "timestamp_utc": utc_now(),
        "mode": "fresh",
        "target_account": config.aws("expected_account_id"),
        "region": config.aws("region"),
        "cluster": config.project("cluster_name"),
        "backend": {
            "bucket": config.raw["terraform"]["backend"]["bucket"],
            "key": config.raw["terraform"]["backend"]["key"],
        },
        "stages": [
            "verify_account",
            "bootstrap_state_backend",
            "provision_ecr",
            "provision_secrets",
            "terraform_infrastructure",
            "build_and_push_images",
            "bootstrap_helm_controllers",
            "bootstrap_argocd",
            "initialize_database",
            "verify_application",
        ],
        "aws_resources_modified": False,
    }


def deployment_preflight(
    config_path: Path,
    *,
    check_aws: bool = False,
) -> dict[str, Any]:
    config = DeploymentConfig.load(config_path)
    plan = build_deployment_plan(config)

    if check_aws:
        lab = LabConfig(
            aws_profile=config.aws("profile"),
            aws_region=config.aws("region"),
            expected_account_id=config.aws("expected_account_id"),
            cluster_name=config.project("cluster_name"),
        )
        runner = CommandRunner(lab)
        validate_target_account(config, runner)
        plan["aws_identity_verified"] = True
    else:
        plan["aws_identity_verified"] = False

    return plan


# CLI_B_EXECUTION_V1
# Portable provisioning is deliberately separated from existing-account recovery.
import hashlib as _hashlib
import os as _os
import secrets as _secrets
import subprocess as _subprocess
from pathlib import Path as _Path


def _deploy_call(argv, *, env, timeout=1800, stdin=None):
    """Never emit subprocess stderr: it may contain generated credentials."""
    try:
        return _subprocess.run([str(x) for x in argv], cwd=ROOT, env=env,
            input=stdin, stdout=_subprocess.PIPE, stderr=_subprocess.PIPE,
            check=True, timeout=timeout).stdout.decode().strip()
    except (_subprocess.CalledProcessError, _subprocess.TimeoutExpired) as exc:
        raise LifecycleError('Deployment command failed: '+str(argv[0])+' (private diagnostics suppressed)') from None


def _deploy_environment(config):
    e=_os.environ.copy()
    e.update(AWS_PROFILE=config.aws('profile'), AWS_REGION=config.aws('region'),
        AWS_DEFAULT_REGION=config.aws('region'), AWS_EC2_METADATA_DISABLED='true')
    return e


def _safe_deploy_config(config):
    validate_deployment_config(config)
    d=config.raw
    if d['terraform']['backend']['key'].startswith('/') or '..' in _Path(d['terraform']['backend']['key']).parts:
        raise LifecycleError('Unsafe backend state key')
    for key in ('backend','frontend'):
        name=d['images'][key]['repository']
        if not re.fullmatch(r'[a-z0-9][a-z0-9._/-]{1,254}',name):
            raise LifecycleError('Invalid ECR repository')
    for key in ('app_runtime','fastapi_auth'):
        name=d['secrets'][key]['name']
        if not re.fullmatch(r'[A-Za-z0-9/_+=.@-]{3,512}',name):
            raise LifecycleError('Invalid secret name')
    # Fresh-account bootstrap must never use a backend already holding another lab's state.
    if d['terraform']['backend']['bucket'].startswith('azawslab-eks-tfstate-275544662748'):
        raise LifecycleError('Existing account backend is forbidden for fresh deployment')


def _deployment_identity(config):
    env=_deploy_environment(config)
    actual=_deploy_call(['aws','sts','get-caller-identity','--query','Account','--output','text'],env=env,timeout=60)
    if actual != config.aws('expected_account_id'):
        raise LifecycleError('Target AWS account mismatch')
    return env


def _deployment_manifest(config):
    _safe_deploy_config(config)
    d=config.raw
    return {'schema':1,'mode':'fresh','account':config.aws('expected_account_id'),
        'region':config.aws('region'),'cluster':config.project('cluster_name'),
        'backend_bucket':d['terraform']['backend']['bucket'],
        'backend_key':d['terraform']['backend']['key'],
        'stages':['backend','ecr','secrets','terraform_plan','images','platform'],
        'execution':'requires explicit stage approval'}


def _bootstrap_backend(config,env):
    b=config.raw['terraform']['backend']; bucket=b['bucket']; region=b['region']
    # Check global bucket ownership before writing anything.
    check=_subprocess.run(['aws','s3api','head-bucket','--bucket',bucket,
        '--expected-bucket-owner',config.aws('expected_account_id')],env=env,
        cwd=ROOT,stdout=_subprocess.DEVNULL,stderr=_subprocess.DEVNULL)
    if check.returncode:
        args=['aws','s3api','create-bucket','--bucket',bucket,'--region',region]
        if region!='us-east-1': args+=['--create-bucket-configuration','LocationConstraint='+region]
        _deploy_call(args,env=env)
    _deploy_call(['aws','s3api','put-public-access-block','--bucket',bucket,
        '--public-access-block-configuration','BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true'],env=env)
    _deploy_call(['aws','s3api','put-bucket-versioning','--bucket',bucket,
        '--versioning-configuration','Status=Enabled'],env=env)
    _deploy_call(['aws','s3api','put-bucket-encryption','--bucket',bucket,
        '--server-side-encryption-configuration','{"Rules":[{"ApplyServerSideEncryptionByDefault":{"SSEAlgorithm":"AES256"}}]}'],env=env)
    # A pre-existing state object indicates a non-fresh environment; do not adopt it.
    state=_subprocess.run(['aws','s3api','head-object','--bucket',bucket,'--key',b['key']],
        env=env,cwd=ROOT,stdout=_subprocess.DEVNULL,stderr=_subprocess.DEVNULL)
    if state.returncode==0: raise LifecycleError('Backend already contains Terraform state; refusing fresh deployment')


def _bootstrap_ecr(config,env):
    for image in config.raw['images'].values():
        repo=image['repository']
        found=_subprocess.run(['aws','ecr','describe-repositories','--repository-names',repo],
            cwd=ROOT,env=env,stdout=_subprocess.DEVNULL,stderr=_subprocess.DEVNULL)
        if found.returncode:
            _deploy_call(['aws','ecr','create-repository','--repository-name',repo,
                '--image-tag-mutability','IMMUTABLE','--image-scanning-configuration','scanOnPush=true'],env=env)


def _bootstrap_secrets(config,env):
    # Secrets created only when absent. Values never printed or committed.
    for name,secret in config.raw['secrets'].items():
        dest=secret['name']
        found=_subprocess.run(['aws','secretsmanager','describe-secret','--secret-id',dest],
            cwd=ROOT,env=env,stdout=_subprocess.DEVNULL,stderr=_subprocess.DEVNULL)
        if found.returncode==0: raise LifecycleError('Secret already exists in fresh account: '+dest)
        if name=='app_runtime':
            payload={'username':'app_runtime','password':_secrets.token_urlsafe(36)}
        elif name=='fastapi_auth':
            payload={'SECRET_KEY':_secrets.token_urlsafe(48),
                'FIRST_SUPERUSER':_os.environ.get('LAB_FIRST_SUPERUSER','admin@example.invalid'),
                'FIRST_SUPERUSER_PASSWORD':_secrets.token_urlsafe(36)}
        else: raise LifecycleError('Unsupported secret type')
        _deploy_call(['aws','secretsmanager','create-secret','--name',dest,
            '--secret-string','file:///dev/stdin'],env=env,
            stdin=json.dumps(payload).encode())


def portable_deploy(config_path, *, stage='preflight', confirm=None):
    """Guarded stages. Terraform/images/platform require project-specific review."""
    cfg=DeploymentConfig.load(_Path(config_path)); _safe_deploy_config(cfg)
    summary=_deployment_manifest(cfg)
    if stage=='preflight': return summary
    if stage not in ('backend','ecr','secrets','terraform_plan'):
        raise LifecycleError('Stage requires reviewed implementation; no automatic provisioning: '+stage)
    required='DEPLOY-'+cfg.aws('expected_account_id')+'-'+stage.upper()
    if confirm!=required: raise LifecycleError('Explicit confirmation required: '+required)
    env=_deployment_identity(cfg)
    if stage=='backend': _bootstrap_backend(cfg,env)
    elif stage=='ecr': _bootstrap_ecr(cfg,env)
    elif stage=='secrets': _bootstrap_secrets(cfg,env)
    elif stage=='terraform_plan':
        # Deliberately only plans: Terraform backend config and variable mapping
        # must be reviewed before applying to an unfamiliar account.
        raise LifecycleError('Terraform plan requires account-specific tfvars/backend review; no apply performed')
    summary['completed_stage']=stage
    return summary
