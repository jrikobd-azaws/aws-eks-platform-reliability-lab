"""Fail-closed single-family lifecycle orchestration over existing native operations.

Default invocation is local-only inspection; prepare/execute require distinct
operator authorisations. This module never manufactures approval tokens.
"""
from __future__ import annotations

import json
import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from foundation import LabConfig, LifecycleError, atomic_json_write, utc_now


@dataclass(frozen=True)
class OperatorApprovals:
    backup: str | None = None
    restore_test: str | None = None
    certification: str | None = None
    shutdown: str | None = None
    rebuild: str | None = None
    database: str | None = None
    gitops: str | None = None


def _require(actual: str | None, expected: str, stage: str) -> None:
    if actual != expected:
        raise LifecycleError(f"Independent {stage} authorisation required")


def _private_file(root: Path, value: str | Path, label: str) -> Path:
    candidate = Path(value).expanduser()
    if not candidate.is_absolute() or candidate.is_symlink():
        raise LifecycleError(f"{label} requires an absolute nonsymlink file")
    resolved = candidate.resolve()
    if not resolved.is_relative_to(root.resolve()) or not resolved.is_file():
        raise LifecycleError(f"{label} not within approved private evidence")
    if resolved.stat().st_mode & 0o077:
        raise LifecycleError(f"{label} has insecure permissions")
    return resolved


class NativeAdapter:
    """Only native, existing source operations; no replacement infrastructure logic."""

    def __init__(self, config: LabConfig):
        self.config = config

    def check_identity(self) -> None:
        from lifecycle import account_guard, current_source_revision
        account_guard(self.config)
        if not re.fullmatch(r"[0-9a-f]{40}", current_source_revision()):
            raise LifecycleError("Unverified source revision")

    def check_shutdown_identity(self) -> None:
        from lifecycle import _verify_shutdown_kubernetes_identity, environment, output, TF
        self.check_identity()
        _verify_shutdown_kubernetes_identity(self.config)
        state = json.loads(output([
            'terraform', f'-chdir={TF}', 'state', 'pull'
        ], env=environment(self.config)))
        if not state.get('lineage') or not isinstance(state.get('serial'), int):
            raise LifecycleError('Terraform state identity unavailable')

    def verify_live_app(self) -> None:
        from recover_platform import validate_recovered_database, validate_recovered_application
        if not validate_recovered_database(self.config):
            raise LifecycleError("Database readiness validation failed")
        if not validate_recovered_application(self.config):
            raise LifecycleError("Application readiness validation failed")

    def new_backup(self) -> Path:
        from lifecycle import backup
        return backup(self.config)

    def verify_backup(self, manifest: Path) -> dict:
        from lifecycle import validate_backup_manifest
        return validate_backup_manifest(self.config, manifest, historical_dev=False)

    def restore_test(self, manifest: Path, confirmation: str) -> Path:
        from lifecycle import restore_test_backup
        return restore_test_backup(self.config, confirmation=confirmation, manifest_path=manifest)

    def verify_restore_test(self, manifest: Path) -> Path:
        from lifecycle import discover_latest_restore_test, verify_backup_restore_provenance
        info = self.verify_backup(manifest)
        report = discover_latest_restore_test(self.config)
        details = json.loads(report.read_text())
        verify_backup_restore_provenance(self.config, info, details)
        return report

    def plan(self, kind: str, tfvars: str) -> Path:
        from lifecycle import create_plan
        return create_plan(self.config, tfvars, kind)

    def verify_plan(self, plan: Path, tfvars: str, kind: str) -> bool:
        """Revalidate plan checksum and current Terraform state without applying."""
        from lifecycle import (checked_inputs, sha256_approved_file, sha256_file,
                               plan_json, assert_plan_policy, environment, call, TF)
        data = json.loads(plan.read_text())
        if (data.get('kind') != kind
                or data.get('account') != self.config.expected_account_id
                or data.get('region') != self.config.aws_region
                or data.get('cluster') != self.config.cluster_name):
            return False
        var = checked_inputs(tfvars)
        if sha256_approved_file(var) != data.get('tfvars_sha256'):
            return False
        planned = Path(data.get('plan', ''))
        if (not planned.is_absolute() or planned.is_symlink()
                or not planned.is_file() or not planned.resolve().is_relative_to(self.root)):
            return False
        if sha256_file(planned) != data.get('plan_sha256'):
            return False
        state = json.loads(call(['terraform', f'-chdir={TF}', 'state', 'pull'],
                                env=environment(self.config)))
        if (state.get('lineage'), state.get('serial')) != (data.get('state_lineage'), data.get('state_serial')):
            return False
        assert_plan_policy(plan_json(self.config, planned),
                           data.get('targets', []),
                           action='delete' if kind == 'shutdown' else 'create')
        return True

    @property
    def root(self) -> Path:
        return self.config.evidence_dir.resolve()

    def verify_shutdown_certification(self, plan: Path) -> bool:
        from lifecycle import require_shutdown_readiness
        require_shutdown_readiness(self.config, json.loads(plan.read_text()))
        return True

    def certify(self, plan: Path, tfvars: str, confirmation: str) -> Path:
        from lifecycle import certify_shutdown
        return certify_shutdown(self.config, plan_report=str(plan), tfvars=tfvars, confirmation=confirmation)

    def apply(self, plan: Path, tfvars: str, confirmation: str) -> None:
        from lifecycle import execute_plan
        execute_plan(self.config, str(plan), confirmation, tfvars)

    def verify_recovery_point_without_live_rds(self, manifest: Path) -> None:
        """Local-only integrity gate for recovery before RDS is rebuilt.

        The full native identity/ownership/restore-test checks run again after
        infrastructure creation, before any PostgreSQL archive replay.
        """
        from lifecycle import current_source_revision
        data = json.loads(manifest.read_text())
        if (data.get("account") != self.config.expected_account_id
                or data.get("cluster") != self.config.cluster_name
                or data.get("source_revision") != current_source_revision()):
            raise LifecycleError("Recovery manifest identity or source mismatch")
        roots = (
            self.config.evidence_dir.resolve(),
            (Path.home() / ".local/share/eks-lab-backups").resolve(),
        )
        for field, digest_key in (("database_backup", "database_sha256"),
                                  ("source_archive", "source_sha256")):
            raw = Path(data.get(field, ""))
            if (not raw.is_absolute() or raw.is_symlink() or not raw.is_file()
                    or not any(raw.resolve().is_relative_to(r) for r in roots)):
                raise LifecycleError("Recovery archive outside approved boundaries")
            digest = hashlib.sha256(raw.read_bytes()).hexdigest()
            if digest != data.get(digest_key):
                raise LifecycleError("Recovery archive checksum mismatch")
        # Backup != proof of restore: independently inspect retained test
        # metadata, then invoke the full native safeguards post-rebuild.
        from lifecycle import discover_latest_restore_test, _verify_backup_restore_order
        test = discover_latest_restore_test(self.config)
        tested = json.loads(test.read_text())
        required = (
            tested.get("status") == "passed"
            and tested.get("source_identity_verified") is True
            and tested.get("restore_tested_to_database") is True
            and tested.get("schema_verified") is True
            and tested.get("runtime_permissions_verified") is True
            and tested.get("database_cleanup_verified") is True
            and tested.get("temporary_resources_cleanup_verified") is True
            and tested.get("source_revision") == data.get("source_revision")
            and tested.get("database_sha256") == data.get("database_sha256")
            and tested.get("account") == self.config.expected_account_id
            and tested.get("cluster") == self.config.cluster_name
            and tested.get("database") == data.get("database")
        )
        if not required:
            raise LifecycleError("Pre-rebuild restore-test provenance mismatch")
        _verify_backup_restore_order(data, tested, historical_dev=False)

    def preflight_db(self, manifest: Path) -> None:
        from recovery import preflight_database_restore
        preflight_database_restore(self.config, str(manifest))

    def restore_platform(self) -> None:
        from recovery import platform
        platform(self.config)

    def platform_ready(self) -> bool:
        from recover_platform import platform_recovery_state_ready
        return bool(platform_recovery_state_ready(self.config))

    def database_ready(self) -> bool:
        from recover_platform import validate_recovered_database
        return bool(validate_recovered_database(self.config))

    def verify_final(self) -> None:
        from foundation import CheckpointStore, CommandRunner
        from recovery import RecoveryManager
        manager = RecoveryManager(
            self.config, CommandRunner(self.config),
            CheckpointStore(self.config.evidence_dir / "python-resume-checkpoint.json"),
        )
        manager.verify_environment()
        self.verify_live_app()

    def restore_db(self, manifest: Path) -> None:
        from recovery import restore_database
        restore_database(self.config, str(manifest))

    def verify_shutdown_complete(self, plan: Path) -> bool:
        """Validate Terraform removal evidence and unchanged state lineage."""
        from lifecycle import output, call, environment, TF
        report = json.loads(plan.read_text())
        targets = report.get('targets', [])
        if not targets or not isinstance(targets, list):
            raise LifecycleError('Shutdown target inventory unavailable')
        state = json.loads(call(['terraform', f'-chdir={TF}', 'state', 'pull'],
                                env=environment(self.config)))
        if state.get('lineage') != report.get('state_lineage'):
            raise LifecycleError('Terraform state lineage changed during shutdown')
        if not isinstance(state.get('serial'), int) or state['serial'] <= report.get('state_serial', -1):
            raise LifecycleError('Terraform state serial did not advance')
        remaining = set(output(['terraform', f'-chdir={TF}', 'state', 'list'],
                               env=environment(self.config)).splitlines())
        if any(target in remaining for target in targets):
            raise LifecycleError('Planned destruction targets remain in Terraform state')
        return True

    def infrastructure_ready(self) -> bool:
        from foundation import CheckpointStore, CommandRunner
        from recovery import RecoveryManager
        manager = RecoveryManager(
            self.config, CommandRunner(self.config),
            CheckpointStore(self.config.evidence_dir / "python-resume-checkpoint.json"),
        )
        return manager.infrastructure_ready()


class OneCommandLifecycle:
    """Persist stage intent; refuse ambiguous interrupted mutations on resume."""

    def __init__(self, config: LabConfig, adapter: Any | None = None):
        self.config = config
        self.adapter = adapter if adapter is not None else NativeAdapter(config)
        self.root = config.evidence_dir.resolve()
        self.record = self.root / "lb3-shutdown-state.json"

    def _select(self, operation: str) -> None:
        if operation not in ("shutdown", "restore"):
            raise LifecycleError("Unsupported lifecycle operation")
        self.record = self.root / f"lb3-{operation}-state.json"

    def _load(self) -> dict:
        if not self.record.exists():
            return {"schema": 1, "stages": {}, "operation": None}
        if self.record.is_symlink() or self.record.stat().st_mode & 0o077:
            raise LifecycleError("Unsafe LB3 orchestration checkpoint")
        data = json.loads(self.record.read_text())
        if data.get("schema") != 1 or not isinstance(data.get("stages"), dict):
            raise LifecycleError("Invalid LB3 orchestration checkpoint")
        return data

    def _save(self, data: dict) -> None:
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        atomic_json_write(self.record, data)

    def _stage(self, state: dict, name: str, action, verify=None):
        prev = state["stages"].get(name, {})
        if prev.get("status") in ("running", "failed"):
            raise LifecycleError(f"Ambiguous previous {name}; independent reconciliation required")
        if prev.get("status") == "passed":
            if verify is None:
                raise LifecycleError(f"Previous {name} requires independent verification")
            if verify():
                return prev.get("result")
            raise LifecycleError(f"Previously completed {name} no longer validates")
        state["stages"][name] = {"status": "running", "time": utc_now()}
        self._save(state)
        try:
            result = action()
            state["stages"][name] = {"status": "passed", "time": utc_now(), "result": str(result) if result is not None else None}
            self._save(state)
            return result
        except Exception:
            state["stages"][name] = {"status": "failed", "time": utc_now()}
            self._save(state)
            raise

    def _state(self, operation: str) -> dict:
        self._select(operation)
        data = self._load()
        if data["operation"] not in (None, operation):
            raise LifecycleError("Existing LB3 lifecycle operation unfinished; preserve checkpoint")
        data["operation"] = operation
        return data

    def _reconciliation_challenge(self, operation: str, stage: str, entry: dict) -> str:
        """Bind explicit operator intent to the exact interrupted stage record."""
        identity = json.dumps(
            {"operation": operation, "stage": stage, "entry": entry},
            sort_keys=True, separators=(",", ":"),
        ).encode("utf-8")
        digest = hashlib.sha256(identity).hexdigest()[:12]
        return (
            f"RECONCILE-{self.config.expected_account_id}-"
            f"{operation.upper()}-{stage.upper()}-{digest}"
        )

    def reconcile(self, operation: str, *, stage: str,
                  approval: str | None, evidence: str | None = None,
                  tfvars: str | None = None) -> dict:
        """Independently verify an interrupted stage; NEVER replay its action.

        Reconciliation is one-way: a failed/running stage can become passed
        only after stage-specific evidence and current-state checks succeed.
        A failed verification leaves the original checkpoint untouched.
        """
        state = self._state(operation)
        record = state["stages"].get(stage)
        if not isinstance(record, dict) or record.get("status") not in ("running", "failed"):
            raise LifecycleError("Reconciliation requires an interrupted stage")
        allowed = {
            "shutdown": ("backup", "restore_test", "shutdown_plan",
                         "shutdown_certification", "shutdown_apply"),
            "restore": ("rebuild_plan", "rebuild_apply", "platform",
                        "database", "application"),
        }
        if stage not in allowed[operation]:
            raise LifecycleError("Unknown reconciliation stage")
        _require(approval, self._reconciliation_challenge(operation, stage, record),
                 "stage reconciliation")
        self.adapter.check_identity()

        artifact = None
        if stage in ("backup", "shutdown_plan", "rebuild_plan"):
            if not evidence:
                raise LifecycleError("Independent private evidence path required")
            artifact = _private_file(self.root, evidence, "reconciliation evidence")
            if stage == "backup":
                if not self.adapter.verify_backup(artifact):
                    raise LifecycleError("Backup reconciliation failed")
            else:
                if not tfvars:
                    raise LifecycleError("Reviewed tfvars required for plan reconciliation")
                kind = "shutdown" if stage == "shutdown_plan" else "rebuild"
                if not self.adapter.verify_plan(artifact, tfvars, kind):
                    raise LifecycleError("Plan reconciliation failed")
        elif stage == "restore_test":
            backup = state["stages"].get("backup", {})
            manifest = _private_file(self.root, backup.get("result") or "",
                                     "completed backup manifest")
            if not self.adapter.verify_backup(manifest):
                raise LifecycleError("Backup evidence invalid")
            if not self.adapter.verify_restore_test(manifest):
                raise LifecycleError("Restore-test evidence invalid")
        elif stage == "shutdown_certification":
            plan = _private_file(self.root, state.get("prepared_plan") or "",
                                 "prepared shutdown plan")
            if not self.adapter.verify_shutdown_certification(plan):
                raise LifecycleError("Shutdown certification invalid")
        elif stage == "shutdown_apply":
            plan = _private_file(self.root, state.get("prepared_plan") or "",
                                 "prepared shutdown plan")
            if not self.adapter.verify_shutdown_complete(plan):
                raise LifecycleError("Shutdown is not independently complete")
        elif stage == "rebuild_apply":
            if not self.adapter.infrastructure_ready():
                raise LifecycleError("Rebuild not independently complete")
        elif stage == "platform":
            if not self.adapter.platform_ready():
                raise LifecycleError("Platform not independently ready")
        elif stage == "database":
            if not self.adapter.database_ready():
                raise LifecycleError("Database not independently restored")
        elif stage == "application":
            self.adapter.verify_live_app()

        # Only independent verification can promote a running/failed entry.
        state["stages"][stage] = {
            "status": "passed", "time": utc_now(),
            "result": str(artifact) if artifact else record.get("result"),
            "reconciled": True, "reconciled_from": record["status"],
        }
        self._save(state)
        return {"operation": operation, "phase": "reconciled", "stage": stage,
                "status": "passed", "action_replayed": False}

    def preflight(self, operation: str) -> dict:
        """Offline-only: never construct a native adapter or call AWS."""
        self._select(operation)
        state = self._load()
        interrupted = {
            stage: self._reconciliation_challenge(operation, stage, entry)
            for stage, entry in state.get("stages", {}).items()
            if isinstance(entry, dict) and entry.get("status") in ("running", "failed")
        }
        return {"operation": operation, "mode": "preflight", "record": str(self.record),
                "current_operation": state.get("operation"), "stages": state.get("stages", {}),
                "reconciliation_challenges": interrupted, "live_execution": False}

    def shutdown(self, *, phase: str, tfvars: str | None = None, report: str | None = None,
                 approvals: OperatorApprovals = OperatorApprovals()) -> dict:
        if phase == "preflight":
            return self.preflight("shutdown")
        state = self._state("shutdown")
        self.adapter.check_identity()
        if phase == "prepare":
            self.adapter.check_shutdown_identity()
            _require(approvals.backup, "BACKUP-" + self.config.expected_account_id, "backup")
            self.adapter.verify_live_app()
            manifest_str = self._stage(state, "backup", self.adapter.new_backup,
                verify=lambda: bool(self.adapter.verify_backup(_private_file(self.root, state["stages"]["backup"]["result"], "backup manifest"))))
            manifest = _private_file(self.root, manifest_str, "backup manifest")
            backup_info = self.adapter.verify_backup(manifest)
            digest = backup_info["database_sha256"]
            token = "RESTORE-TEST-" + self.config.expected_account_id + "-" + digest[:12]
            _require(approvals.restore_test, token, "restore-test")
            self._stage(state, "restore_test", lambda: self.adapter.restore_test(manifest, token),
                verify=lambda: bool(self.adapter.verify_restore_test(manifest)))
            if not tfvars:
                raise LifecycleError("Reviewed tfvars required")
            # Do not auto-repeat Terraform plans. An interrupted plan is ambiguous.
            plan = self._stage(state, "shutdown_plan", lambda: self.adapter.plan("shutdown", tfvars),
                verify=lambda: self.adapter.verify_plan(_private_file(self.root, state["stages"]["shutdown_plan"]["result"], "shutdown plan"), tfvars, "shutdown"))
            state["prepared_plan"] = str(plan)
            state["prepared_manifest"] = str(manifest)
            self._save(state)
            return {"operation": "shutdown", "phase": "prepared", "plan": str(plan),
                    "manifest": str(manifest), "destruction_authorized": False}
        if phase == "execute":
            if not tfvars or not report:
                raise LifecycleError("Reviewed tfvars and saved plan required")
            saved = _private_file(self.root, report, "shutdown plan")
            if str(saved) != state.get("prepared_plan"):
                raise LifecycleError("Shutdown report is not the prepared plan")
            manifest = _private_file(self.root, state.get("prepared_manifest", ""), "prepared backup")
            self.adapter.verify_backup(manifest)
            self.adapter.verify_restore_test(manifest)
            plan_data = json.loads(saved.read_text())
            certify_token = "CERTIFY-" + self.config.expected_account_id + "-" + plan_data["plan_sha256"][:12]
            _require(approvals.certification, certify_token, "shutdown certification")
            self._stage(state, "shutdown_certification", lambda: self.adapter.certify(saved, tfvars, certify_token),
                verify=lambda: self.adapter.verify_shutdown_certification(saved))
            _require(approvals.shutdown, "SHUTDOWN-" + self.config.expected_account_id, "shutdown")
            self._stage(state, "shutdown_apply", lambda: self.adapter.apply(saved, tfvars, approvals.shutdown),
                verify=lambda: self.adapter.verify_shutdown_complete(saved))
            if not self.adapter.verify_shutdown_complete(saved):
                raise LifecycleError("Selective shutdown not independently verified")
            state["completed"] = True
            self._save(state)
            return {"operation": "shutdown", "phase": "complete", "verified": True}
        raise LifecycleError("Unknown lifecycle phase")

    def restore(self, *, phase: str, tfvars: str | None = None, report: str | None = None,
                manifest: str | None = None,
                approvals: OperatorApprovals = OperatorApprovals()) -> dict:
        if phase == "preflight":
            return self.preflight("restore")
        state = self._state("restore")
        self.adapter.check_identity()
        if not manifest:
            raise LifecycleError("Explicit recovery manifest required")
        source = _private_file(self.root, manifest, "recovery manifest")
        self.adapter.verify_recovery_point_without_live_rds(source)
        if phase == "prepare":
            if not tfvars:
                raise LifecycleError("Reviewed tfvars required")
            plan = self._stage(state, "rebuild_plan", lambda: self.adapter.plan("rebuild", tfvars),
                verify=lambda: self.adapter.verify_plan(_private_file(self.root, state["stages"]["rebuild_plan"]["result"], "rebuild plan"), tfvars, "rebuild"))
            state["prepared_plan"] = str(plan)
            state["prepared_manifest"] = str(source)
            self._save(state)
            return {"operation": "restore", "phase": "prepared", "plan": str(plan),
                    "manifest": str(source), "rebuild_authorized": False}
        if phase == "execute":
            if not report or not tfvars:
                raise LifecycleError("Saved rebuild plan and tfvars required")
            saved = _private_file(self.root, report, "rebuild plan")
            if str(saved) != state.get("prepared_plan") or str(source) != state.get("prepared_manifest"):
                raise LifecycleError("Recovery inputs differ from prepared approval")
            _require(approvals.rebuild, "REBUILD-" + self.config.expected_account_id, "rebuild")
            self._stage(state, "rebuild_apply", lambda: self.adapter.apply(saved, tfvars, approvals.rebuild),
                verify=self.adapter.infrastructure_ready)
            if not self.adapter.infrastructure_ready():
                raise LifecycleError("Infrastructure rebuild not verified")
            self.adapter.verify_backup(source)
            self.adapter.verify_restore_test(source)
            _require(approvals.gitops, "GITOPS-" + self.config.expected_account_id, "selective GitOps recovery")
            self._stage(state, "platform", self.adapter.restore_platform,
                verify=self.adapter.platform_ready)
            _require(approvals.database, "RESTORE-" + self.config.expected_account_id, "database restoration")
            self.adapter.preflight_db(source)
            # Actual emptiness is checked within native restore_database before pg_restore.
            self._stage(state, "database", lambda: self.adapter.restore_db(source),
                verify=self.adapter.database_ready)
            self._stage(state, "application", self.adapter.verify_final,
                verify=lambda: (self.adapter.verify_live_app() is None))
            state["completed"] = True
            self._save(state)
            return {"operation": "restore", "phase": "complete", "verified": True}
        raise LifecycleError("Unknown lifecycle phase")
