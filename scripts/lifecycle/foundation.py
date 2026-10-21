"""Shared lifecycle safety, configuration and checkpoint utilities."""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence


ROOT = Path(__file__).resolve().parents[2]
EVIDENCE = ROOT / ".private-evidence" / "lifecycle"


class LifecycleError(RuntimeError):
    """Controlled lifecycle failure."""


@dataclass(frozen=True)
class LabConfig:
    """Runtime configuration; never embed AWS credentials."""

    aws_profile: str = "eks-lab-terraform"
    aws_region: str = "eu-west-1"
    expected_account_id: str = "275544662748"
    cluster_name: str = "eks-platform-reliability-lab"
    terraform_dir: Path = ROOT / "infra/terraform/environments/lab"
    evidence_dir: Path = EVIDENCE

    @classmethod
    def from_environment(cls) -> "LabConfig":
        profile = os.getenv("LAB_AWS_PROFILE", "eks-lab-terraform")
        region = os.getenv("LAB_AWS_REGION", "eu-west-1")
        account = os.getenv("LAB_EXPECTED_ACCOUNT_ID", "275544662748")
        cluster = os.getenv(
            "LAB_CLUSTER_NAME", "eks-platform-reliability-lab"
        )
        evidence_value = os.getenv("LAB_LIFECYCLE_EVIDENCE_DIR")
        evidence = Path(evidence_value).expanduser() if evidence_value else EVIDENCE
        if not evidence_value and not (evidence / "python-platform-recovery-checkpoint.json").is_file():
            # Reuse one existing authoritative checkpoint across clean worktrees.
            # Never create a competing checkpoint when historical identity is ambiguous.
            candidates = []
            projects = Path.home() / "projects"
            if projects.is_dir():
                for candidate in projects.glob("aws-eks-*/.private-evidence/lifecycle"):
                    checkpoint = candidate / "python-platform-recovery-checkpoint.json"
                    if (checkpoint.is_file() and not checkpoint.is_symlink() and
                            not (candidate.is_symlink() and not candidate.resolve().is_dir())):
                        candidates.append(candidate.resolve())
            unique = sorted(set(candidates))
            if len(unique) == 1:
                evidence = unique[0]
            elif len(unique) > 1:
                raise LifecycleError("Multiple authoritative recovery checkpoints; select verified evidence explicitly")

        if not evidence.is_absolute():
            raise LifecycleError("Lifecycle evidence directory must be absolute")

        if not re.fullmatch(r"[0-9]{12}", account):
            raise LifecycleError("Invalid AWS account ID")

        if not re.fullmatch(r"[a-z]{2}-[a-z]+-[0-9]+", region):
            raise LifecycleError("Invalid AWS region")

        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{1,99}", cluster):
            raise LifecycleError("Invalid cluster name")

        return cls(
            aws_profile=profile,
            aws_region=region,
            expected_account_id=account,
            cluster_name=cluster,
            evidence_dir=evidence,
        )

    def environment(self) -> dict[str, str]:
        env = os.environ.copy()
        env["AWS_PROFILE"] = self.aws_profile
        env["AWS_REGION"] = self.aws_region
        env["AWS_DEFAULT_REGION"] = self.aws_region
        env["AWS_EC2_METADATA_DISABLED"] = "true"
        return env


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def redact(text: str, secrets: Sequence[str] = ()) -> str:
    """Remove explicitly supplied secret values from diagnostic text."""
    result = str(text)

    for secret in secrets:
        if secret:
            result = result.replace(secret, "[REDACTED]")

    result = re.sub(
        r"(?i)\b(password|passwd|token|authorization|secret|connection[-_ ]?string)\b"
        r"\s*[:=]\s*\S+",
        r"\1=[REDACTED]",
        result,
    )
    result = re.sub(r"(?i)postgres(?:ql)?://\S+", "[REDACTED_DSN]", result)
    result = re.sub(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b", "[REDACTED_AWS_KEY]", result)

    return result


def atomic_json_write(path: Path, data: Mapping[str, Any]) -> None:
    """Write a JSON checkpoint atomically with restrictive permissions."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    fd, temp_name = tempfile.mkstemp(
        prefix=".checkpoint-",
        suffix=".tmp",
        dir=path.parent,
    )

    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(data, stream, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())

        os.chmod(temp_name, 0o600)
        os.replace(temp_name, path)

    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


class CheckpointStore:
    """Persistent stage state for resumable lifecycle operations."""

    VALID_STATES = frozenset({
        "pending", "running", "passed", "failed", "skipped"
    })

    def __init__(self, path: Path):
        self.path = Path(path)

    def load(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"schema_version": 1, "stages": {}}

        with self.path.open(encoding="utf-8") as stream:
            data = json.load(stream)

        if not isinstance(data, dict):
            raise LifecycleError("Checkpoint must be a JSON object")

        if data.get("schema_version") != 1:
            raise LifecycleError("Unsupported checkpoint version")

        stages = data.get("stages")
        if not isinstance(stages, dict):
            raise LifecycleError("Invalid checkpoint stages")

        for stage_name, stage_info in stages.items():
            if not isinstance(stage_info, dict):
                raise LifecycleError(f"Invalid stage info structure for stage: {stage_name}")
            st = stage_info.get("status")
            if st not in self.VALID_STATES:
                raise LifecycleError(f"Invalid status {st!r} for stage {stage_name}")

        return data

    def update(
        self,
        stage: str,
        status: str,
        *,
        detail: str = "",
    ) -> None:
        if status not in self.VALID_STATES:
            raise LifecycleError(f"Invalid stage status: {status}")

        data = self.load()
        data["stages"][stage] = {
            "status": status,
            "updated_utc": utc_now(),
            "detail": detail,
        }

        atomic_json_write(self.path, data)

    def reconcile_identity(self, identity: Mapping[str, Any]) -> bool:
        """Bind checkpoints to verified infrastructure and source identity.

        Returns True when an existing binding was reused. A genuine identity
        change invalidates stage completion locally; malformed or partial
        identity data fails closed instead of making historical evidence valid.
        """
        required = {
            "account_id", "region", "cluster_arn", "terraform_workspace",
            "terraform_lineage", "terraform_serial", "source_revision",
            "evidence_root",
        }
        if set(identity) != required or any(
            value in (None, "") for value in identity.values()
        ):
            raise LifecycleError("Incomplete recovery checkpoint identity")

        normalized = {key: identity[key] for key in sorted(required)}
        fingerprint = hashlib.sha256(
            json.dumps(normalized, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        data = self.load()
        existing = data.get("identity")
        if existing is not None and not isinstance(existing, dict):
            raise LifecycleError("Invalid checkpoint identity")

        if existing:
            stored_values = existing.get("values")
            stored_fingerprint = existing.get("fingerprint")
            if not isinstance(stored_values, dict) or not isinstance(stored_fingerprint, str):
                raise LifecycleError("Invalid checkpoint identity")
            calculated = hashlib.sha256(
                json.dumps(stored_values, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest()
            if calculated != stored_fingerprint:
                raise LifecycleError("Checkpoint identity fingerprint mismatch")

        stable = ("account_id", "region", "cluster_arn", "terraform_workspace", "terraform_lineage", "evidence_root")
        reused = bool(existing and all(
            existing["values"].get(key) == normalized[key] for key in stable
        ))
        if not reused:
            for stage, stage_info in data["stages"].items():
                if stage_info.get("status") in {"passed", "running"}:
                    data["stages"][stage] = {
                        "status": "pending",
                        "updated_utc": utc_now(),
                        "detail": "Invalidated after verified identity change",
                    }

        data["identity"] = {
            "fingerprint": fingerprint,
            "values": normalized,
            "updated_utc": utc_now(),
        }
        atomic_json_write(self.path, data)
        return reused

    def completed(self, stage: str) -> bool:
        return (
            self.load()["stages"].get(stage, {}).get("status")
            == "passed"
        )


class CommandRunner:
    """Structured subprocess execution with bounded timeouts."""

    def __init__(
        self,
        config: LabConfig,
        *,
        dry_run: bool = False,
    ):
        self.config = config
        self.dry_run = dry_run

    def run(
        self,
        args: Sequence[str | Path],
        *,
        timeout: int = 900,
        cwd: Path | None = None,
        secrets: Sequence[str] = (),
        env: Mapping[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:

        command = [str(value) for value in args]

        if not command:
            raise LifecycleError("Empty command")

        if timeout <= 0:
            raise LifecycleError("Timeout must be positive")

        if self.dry_run:
            return subprocess.CompletedProcess(
                args=command,
                returncode=0,
                stdout="",
                stderr="",
            )

        try:
            return subprocess.run(
                command,
                cwd=cwd or ROOT,
                env=dict(env) if env is not None else self.config.environment(),
                text=True,
                capture_output=True,
                timeout=timeout,
                check=True,
            )

        except subprocess.TimeoutExpired as exc:
            raise LifecycleError(
                f"Command timed out: {command[0]}"
            ) from exc

        except subprocess.CalledProcessError as exc:
            raw_err = (exc.stderr or exc.stdout or "").lower()
            patterns = (
                ("BACKEND_NOT_INITIALIZED", "backend initialization required"),
                ("BACKEND_NOT_INITIALIZED", "terraform init"),
                ("AWS_AUTHENTICATION", "expiredtoken"),
                ("AWS_AUTHENTICATION", "invalidclienttokenid"),
                ("ACCESS_DENIED", "accessdenied"),
                ("KUBERNETES_AUTH", "unauthorized"),
                ("KUBERNETES_AUTH", "forbidden"),
                ("TIMEOUT", "deadline exceeded"),
                ("ARGOCD_AUTH", "unauthenticated"),
                ("RESOURCE_CONFLICT", "alreadyexists"),
            )
            category = next((label for label, phrase in patterns if phrase in raw_err),
                            "UNCLASSIFIED")
            # Never emit arbitrary stderr: provider output may include secrets.
            raise LifecycleError(
                f"Command failed: {Path(command[0]).name} "
                f"(exit={exc.returncode}; category={category}; private stderr [REDACTED])"
            ) from None


def verify_account(
    runner: CommandRunner,
    expected: str,
) -> None:
    """Reject operations in an unexpected AWS account."""
    result = runner.run([
        "aws",
        "sts",
        "get-caller-identity",
        "--query",
        "Account",
        "--output",
        "text",
    ], timeout=60)

    if result.stdout.strip() != expected:
        raise LifecycleError("Unexpected AWS account")


def validate_stage_sequence(
    checkpoint: CheckpointStore,
    prerequisites: Sequence[str],
) -> None:
    """Require earlier lifecycle stages to be completed."""
    missing = [
        stage for stage in prerequisites
        if not checkpoint.completed(stage)
    ]

    if missing:
        raise LifecycleError(
            "Incomplete prerequisites: " + ", ".join(missing)
        )
