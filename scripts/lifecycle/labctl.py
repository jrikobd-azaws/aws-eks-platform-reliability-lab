#!/usr/bin/env python3
"""AzAWSLab lifecycle controller.

Python entry point for verified recovery operations.
Legacy Bash operations remain available until fully migrated.
"""

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LC = Path(__file__).resolve().parent
TF = ROOT / "infra/terraform/environments/lab"
EVIDENCE = ROOT / ".private-evidence/lifecycle"

ACCOUNT = "275544662748"
CLUSTER = "eks-platform-reliability-lab"
REGION = "eu-west-1"

os.environ.setdefault("AWS_PROFILE", "eks-lab-terraform")
os.environ["AWS_REGION"] = REGION
os.environ["AWS_DEFAULT_REGION"] = REGION


def execute(args, *, timeout=900, capture=False):
    args = [str(x) for x in args]
    result = subprocess.run(
        args,
        cwd=ROOT,
        text=True,
        capture_output=capture,
        timeout=timeout,
        check=True,
    )
    return result.stdout.strip() if capture else ""


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def verify_account():
    account = execute(
        [
            "aws", "sts", "get-caller-identity",
            "--query", "Account", "--output", "text",
        ],
        capture=True,
    )
    require(account == ACCOUNT, "Unexpected AWS account")
    print("PASS: AWS account verified")


def terraform_outputs():
    raw = execute(
        ["terraform", f"-chdir={TF}", "output", "-json"],
        capture=True,
    )
    data = json.loads(raw)
    return {key: value["value"] for key, value in data.items()}


def kubectl_json(*args):
    return json.loads(
        execute(["kubectl", *args, "-o", "json"], capture=True)
    )


def verify():
    results = {}
    verify_account()

    print("=== TERRAFORM ===")
    execute(["terraform", f"-chdir={TF}", "validate"])

    outputs = terraform_outputs()
    require(outputs["cluster_name"] == CLUSTER, "Cluster mismatch")

    results["terraform"] = "passed"
    print("PASS: Terraform configuration and outputs")

    print("=== EKS ===")
    execute([
        "aws", "eks", "update-kubeconfig",
        "--name", CLUSTER,
        "--region", REGION,
        "--alias", "eks-reliability-lab",
    ])

    require(
        execute(
            ["kubectl", "config", "current-context"],
            capture=True,
        ) == "eks-reliability-lab",
        "Unexpected Kubernetes context",
    )

    nodes = kubectl_json("get", "nodes")["items"]
    require(len(nodes) > 0, "No EKS nodes")

    for node in nodes:
        conditions = {
            item["type"]: item["status"]
            for item in node["status"]["conditions"]
        }
        require(
            conditions.get("Ready") == "True",
            f"Node not Ready: {node['metadata']['name']}",
        )

    results["eks_nodes"] = len(nodes)
    print(f"PASS: {len(nodes)} EKS nodes Ready")

    print("=== RDS ===")
    rds = json.loads(
        execute([
            "aws", "rds", "describe-db-instances",
            "--db-instance-identifier", CLUSTER + "-postgres",
            "--output", "json",
        ], capture=True)
    )["DBInstances"][0]

    require(rds["DBInstanceStatus"] == "available", "RDS unavailable")
    require(
        rds["Endpoint"]["Address"] == outputs["rds_address"],
        "RDS endpoint mismatch",
    )

    results["rds"] = "available"
    print("PASS: Current RDS endpoint")

    print("=== HELM CONTROLLERS ===")
    releases = json.loads(
        execute(["helm", "list", "-A", "-o", "json"], capture=True)
    )

    deployed = {
        (item["name"], item["namespace"])
        for item in releases
        if item.get("status") == "deployed"
    }

    required = {
        ("argocd", "argocd"),
        ("external-secrets", "platform-system"),
        ("aws-load-balancer-controller", "kube-system"),
    }

    require(
        required.issubset(deployed),
        f"Missing Helm releases: {sorted(required - deployed)}",
    )

    results["helm_controllers"] = "passed"
    print("PASS: All three platform Helm releases")

    print("=== EXTERNAL SECRETS ===")
    for name in ("rds-credentials", "fastapi-auth"):
        obj = kubectl_json(
            "-n", "dev", "get", "externalsecret", name
        )
        ready = any(
            condition.get("type") == "Ready"
            and condition.get("status") == "True"
            for condition in obj.get("status", {}).get("conditions", [])
        )
        require(ready, f"ExternalSecret not Ready: {name}")

        secret = kubectl_json("-n", "dev", "get", "secret", name)
        require(bool(secret.get("data")), f"Secret empty: {name}")
        print(f"PASS: {name} synchronized")

    results["external_secrets"] = "passed"

    print("=== ARGO CD ===")
    applications = kubectl_json(
        "-n", "argocd", "get", "applications"
    )["items"]

    app_status = {}

    for item in applications:
        name = item["metadata"]["name"]
        status = item.get("status", {})
        sync = status.get("sync", {}).get("status", "Unknown")
        health = status.get("health", {}).get("status", "Unknown")

        app_status[name] = {
            "sync": sync,
            "health": health,
        }
        print(f"{name}: sync={sync}, health={health}")

    require("platform-root" in app_status, "Argo CD root missing")
    require(
        app_status["platform-root"]["health"] == "Healthy",
        "Argo CD root unhealthy",
    )

    results["argocd"] = app_status

    print("=== APPLICATION CONFIGURATION ===")
    execute([
        "helm", "lint", "app/helm",
        "-f", "app/helm/values-dev.yaml",
    ])

    execute([
        "helm", "template", "eks-platform-app",
        "app/helm", "--namespace", "dev",
        "-f", "app/helm/values-dev.yaml",
    ], capture=True)

    results["application_helm"] = "passed"
    print("PASS: Application Helm rendering")

    print("=== SOURCE VALIDATION ===")
    execute(["git", "diff", "--check"])

    results["git"] = "passed"

    # Historical integration evidence is not a live application test.
    results["fastapi_rds_live_test"] = "not_executed"
    results["frontend_api_live_test"] = "not_executed"

    EVIDENCE.mkdir(parents=True, exist_ok=True)
    report = EVIDENCE / "python-recovery-acceptance.json"

    report.write_text(json.dumps({
        "timestamp_utc": datetime.now(
            timezone.utc
        ).isoformat(),
        "results": results,
        "full_application_recovery_verified": False,
    }, indent=2) + "\n")
    report.chmod(0o600)

    print("PASS: Platform recovery acceptance")
    print("REPORT:", report)
    print("NOTE: Live FastAPI/RDS and frontend routing tests not run")


def status():
    verify_account()
    outputs = terraform_outputs()
    print(json.dumps({
        "cluster": outputs.get("cluster_name"),
        "vpc": outputs.get("vpc_id"),
        "rds": outputs.get("rds_address"),
    }, indent=2))


def legacy(command):
    mapping = {
        "shutdown-plan": [
            "bash",
            LC / "stage3-selective-shutdown-plan.sh",
            "--plan",
        ],
        "resume-platform": [
            sys.executable,
            LC / "recover_platform.py",
        ],
    }
    execute(mapping[command], timeout=3600)



# STAGE3_APPLICATION_ACCEPTANCE_V1
def verify_application():
    """Run the existing live EKS/RDS integration validator."""
    verify_account()

    image = (
        "275544662748.dkr.ecr.eu-west-1.amazonaws.com/"
        "eks-platform-reliability-lab/backend@"
        "sha256:a2ef52d32a137d772e62cc9883570c7c8f4222bd32a26dcce6a058b597c707e8"
    )

    env = os.environ.copy()
    env["LAB_FASTAPI_RDS_APPROVED"] = "YES"
    env["LAB_BACKEND_IMAGE"] = image

    print("=== LIVE FASTAPI/RDS VALIDATION ===", flush=True)

    subprocess.run(
        [
            "bash",
            "scripts/validate-stage3-batch-a.sh",
            "fastapi-rds",
        ],
        cwd=ROOT,
        env=env,
        check=True,
        timeout=900,
    )

    print("PASS: Live FastAPI/RDS validator completed")


def verify_frontend_configuration():
    """Check frontend routing and immutable image availability."""
    verify_account()

    nginx = (
        ROOT / "app/frontend/nginx.eks.conf"
    ).read_text()

    require(
        "eks-platform-backend" in nginx,
        "Frontend Nginx backend Service reference missing",
    )

    digest = (
        "sha256:6f41d5a510108d4f4df14635d482af701ea0247319b6869c75b1f7a513155a5b"
    )

    execute([
        "aws", "ecr", "describe-images",
        "--repository-name",
        "eks-platform-reliability-lab/frontend",
        "--image-ids", f"imageDigest={digest}",
    ], capture=True)

    execute([
        "helm", "template",
        "eks-platform-app",
        "app/helm",
        "--namespace", "dev",
        "-f", "app/helm/values-dev.yaml",
    ], capture=True)

    print("PASS: Frontend configuration and ECR image verified")
    print("NOTE: Live frontend-to-backend HTTP routing not tested")



# STAGE3_PYTHON_LIFECYCLE_PHASE2
def lifecycle_manager():
    from foundation import LabConfig, CommandRunner, CheckpointStore
    from lifecycle import LifecycleManager

    config = LabConfig.from_environment()
    return LifecycleManager(
        config=config,
        runner=CommandRunner(config),
        checkpoint=CheckpointStore(
            config.evidence_dir / "python-lifecycle-checkpoint.json"
        ),
    )


def python_backup():
    result = lifecycle_manager().backup()
    print(json.dumps(result, indent=2))


def python_shutdown_plan():
    result = lifecycle_manager().plan_shutdown()
    print(json.dumps({
        "status": result["status"],
        "plan": result["plan"],
        "validation": result["validation"],
    }, indent=2))


def python_shutdown_execute():
    expected = "DESTROY-" + lifecycle_manager().config.expected_account_id

    print(
        "WARNING: This will destroy the approved disposable AWS lab."
    )
    print("Type", expected, "to continue.")

    try:
        with open("/dev/tty", encoding="utf-8") as terminal:
            confirmation = terminal.readline().strip()
    except OSError:
        raise RuntimeError("Interactive terminal required")

    result = lifecycle_manager().execute_shutdown(
        confirmation=confirmation
    )
    print(json.dumps(result, indent=2))



# STAGE3_PYTHON_RECOVERY_PHASE3
def python_resume(execute=False):
    from bootstrap import RecoveryBootstrap
    from foundation import LabConfig, CommandRunner, CheckpointStore
    from recovery import RecoveryManager

    config = LabConfig.from_environment()

    manager = RecoveryManager(
        config=config,
        runner=CommandRunner(config),
        checkpoint=CheckpointStore(
            config.evidence_dir / "python-resume-checkpoint.json"
        ),
        bootstrap=RecoveryBootstrap(config),
    )

    report = manager.resume(execute=execute)
    print(json.dumps(report, indent=2))



# STAGE3_PORTABLE_DEPLOYMENT_PHASE4
def python_deploy_preflight(config_path, check_aws=False):
    from deployment import deployment_preflight
    from foundation import atomic_json_write

    plan = deployment_preflight(
        Path(config_path),
        check_aws=check_aws,
    )

    path = (
        EVIDENCE / "python-deployment-preflight.json"
    )

    atomic_json_write(path, plan)
    print(json.dumps(plan, indent=2))
    print("REPORT:", path)
    print("NOTE: No AWS resources created")



# CLI_A_NATIVE_ROUTING_V2
def native_action(command,args):
    from foundation import LabConfig, LifecycleError
    from lifecycle import (
        backup,
        certify_shutdown,
        create_plan,
        execute_plan,
        restore_test_backup,
    )
    from recovery import preflight_database_restore, restore_database, platform
    config=LabConfig.from_environment()
    if command=='native-backup': backup(config)
    elif command=='native-shutdown-plan': create_plan(config,args.tfvars,'shutdown')
    elif command=='native-rebuild-plan': create_plan(config,args.tfvars,'rebuild')
    elif command in ('native-shutdown-execute','native-rebuild-execute'):
        if not args.report or not args.confirm: raise LifecycleError('Saved --report and --confirm required')
        execute_plan(config,args.report,args.confirm,args.tfvars)
    elif command=='native-database-restore':
        if not args.report or args.confirm!='RESTORE-'+config.expected_account_id:
            raise LifecycleError('Explicit RESTORE-account confirmation and manifest required')
        restore_database(config,args.report)
    elif command=='native-database-preflight':
        preflight_database_restore(config, args.report)
    elif command=='native-platform-restore': platform(config)
    elif command=='native-restore-test':
        restore_test_backup(
            config, confirmation=args.confirm, manifest_path=args.report
        )
    elif command=='native-shutdown-certify':
        if not args.report or not args.tfvars:
            raise LifecycleError('Shutdown plan --report and --tfvars are required')
        certify_shutdown(
            config, plan_report=args.report, tfvars=args.tfvars,
            confirmation=args.confirm,
        )
    else: raise LifecycleError('Unknown native operation')



# CLI_B_CLI_V1
def portable_stage_cli(config_path, stage, confirm):
    from deployment import portable_deploy
    print(json.dumps(
        portable_deploy(config_path, stage=stage, confirm=confirm),
        indent=2
    ))


def main():
    parser = argparse.ArgumentParser(
        description="AzAWSLab lifecycle controller"
    )

    parser.add_argument('--tfvars', help='Private reviewed tfvars JSON, chmod 600')
    parser.add_argument('--report', help='Saved private plan report or recovery manifest')
    parser.add_argument('--confirm', help='Explicit account-specific approval')

    # LB3 safe-by-default command family. All execution approvals are separate.
    parser.add_argument('--lb3-phase', choices=('preflight', 'prepare', 'execute', 'reconcile'), default='preflight')
    parser.add_argument('--reconcile-stage', help='Interrupted LB3 stage; no action replay')
    parser.add_argument('--reconcile-evidence', help='Private manifest or plan for reconciliation')
    parser.add_argument('--reconcile-approval', help='Checkpoint-bound independent reconciliation approval')
    parser.add_argument('--manifest', help='Explicit approved recovery manifest for restore')
    parser.add_argument('--backup-approval', help='Explicit live backup authorisation')
    parser.add_argument('--restore-test-approval', help='Explicit isolated restore-test authorisation')
    parser.add_argument('--certification-approval', help='Explicit certification authorisation')
    parser.add_argument('--shutdown-approval', help='Explicit selective shutdown authorisation')
    parser.add_argument('--rebuild-approval', help='Explicit infrastructure rebuild authorisation')
    parser.add_argument('--database-approval', help='Explicit database restoration authorisation')
    parser.add_argument('--gitops-approval', help='Explicit selective GitOps authorisation')

    parser.add_argument(
        "--deploy-stage",
        default="preflight",
        choices=("preflight", "backend", "ecr", "secrets", "terraform_plan")
    )
    parser.add_argument("--deploy-confirm", default=None)

    parser.add_argument(
        "--config",
        default="config/lab.example.json",
        help="Fresh deployment configuration"
    )

    parser.add_argument(
        "--check-aws",
        action="store_true",
        help="Verify configured AWS account without modifying it"
    )

    parser.add_argument(
        "--execute", action="store_true",
        help="Execute approved recovery stages"
    )

    parser.add_argument(
        "--plan", action="store_true",
        help="Generate a shutdown plan without applying it"
    )

    parser.add_argument(
        "command",
        choices=[
            "status",
            "verify",
            "shutdown-plan",
            "resume-platform",
            "verify-app",
            "verify-frontend",
            "backup",
            "shutdown",
            "restore",
            "resume",
            "deploy",
            "native-backup",
            "native-shutdown-plan",
            "native-shutdown-execute",
            "native-rebuild-plan",
            "native-rebuild-execute",
            "native-database-restore",
            "native-database-preflight",
            "native-platform-restore",
            "native-restore-test",
            "native-shutdown-certify",
        ],
    )

    args = parser.parse_args()

    if args.command.startswith('native-'):
        native_action(args.command,args)
    elif args.command == "deploy" and args.deploy_stage != "preflight":
        portable_stage_cli(
            args.config,
            args.deploy_stage,
            args.deploy_confirm,
        )
    elif args.command == "deploy":
        python_deploy_preflight(
            args.config,
            check_aws=args.check_aws,
        )
    elif args.command == "resume":
        python_resume(execute=args.execute)
    elif args.command == "backup":
        python_backup()
    elif args.command in ("shutdown", "restore"):
        from foundation import LabConfig
        from orchestrate import OneCommandLifecycle, OperatorApprovals
        coordinator = OneCommandLifecycle(LabConfig.from_environment())
        approvals = OperatorApprovals(
            backup=args.backup_approval,
            restore_test=args.restore_test_approval,
            certification=args.certification_approval,
            shutdown=args.shutdown_approval,
            rebuild=args.rebuild_approval,
            database=args.database_approval,
            gitops=args.gitops_approval,
        )
        if args.plan and args.lb3_phase != 'preflight':
            raise RuntimeError('--plan cannot bypass LB3 phase boundaries')
        if args.lb3_phase == "reconcile":
            result = coordinator.reconcile(
                args.command, stage=args.reconcile_stage,
                approval=args.reconcile_approval,
                evidence=args.reconcile_evidence, tfvars=args.tfvars,
            )
        elif args.command == "shutdown":
            result = coordinator.shutdown(
                phase=args.lb3_phase, tfvars=args.tfvars,
                report=args.report, approvals=approvals,
            )
        else:
            result = coordinator.restore(
                phase=args.lb3_phase, tfvars=args.tfvars,
                report=args.report, manifest=args.manifest,
                approvals=approvals,
            )
        print(json.dumps(result, indent=2))
    elif args.command == "verify":
        verify()
    elif args.command == "verify-app":
        verify_application()
    elif args.command == "verify-frontend":
        verify_frontend_configuration()
    elif args.command == "status":
        status()
    else:
        verify_account()
        legacy(args.command)


def _sanitized_lifecycle_detail(exc):
    """Return bounded diagnostics only for controlled lifecycle failures."""
    from foundation import LifecycleError, redact

    if not isinstance(exc, LifecycleError):
        return None
    detail = redact(str(exc)).replace("\r", " ").replace("\n", " ")[:500]
    cause = exc.__cause__
    if isinstance(cause, LifecycleError):
        cause_detail = redact(str(cause)).replace("\r", " ").replace("\n", " ")[:500]
        if cause_detail and cause_detail != detail:
            detail += "; cause=" + cause_detail
    return detail or "controlled lifecycle failure without detail"


def blocked_error_message(exc, operation=None):
    """Fail closed while exposing only sanitized controlled diagnostics."""
    stage = (
        "native-database-restore"
        if operation == "native-database-restore"
        else "lifecycle"
    )
    detail = _sanitized_lifecycle_detail(exc)
    if detail is None:
        detail = "unexpected failure; exception detail suppressed"
    return (
        f"BLOCKED: stage={stage}; category={type(exc).__name__}; "
        f"detail={detail}. Review private diagnostic evidence before retry."
    )


if __name__ == "__main__":
    try:
        main()
    except (
        RuntimeError,
        subprocess.CalledProcessError,
        subprocess.TimeoutExpired,
        OSError,
        KeyError,
        ValueError,
    ) as exc:
        print(
            blocked_error_message(
                exc,
                operation=(
                    "native-database-restore"
                    if "native-database-restore" in sys.argv[1:]
                    else None
                ),
            ),
            file=sys.stderr,
        )
        sys.exit(1)
