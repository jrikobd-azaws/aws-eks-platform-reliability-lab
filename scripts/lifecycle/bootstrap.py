"""Verified, idempotent execution bootstrap for native lifecycle recovery."""

from __future__ import annotations

import contextlib
import collections
import http.client
import ssl
import base64
import hashlib
import json
import os
import re
import shutil
import socket
import stat
import subprocess
import tempfile
import time
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Callable, Iterator

import yaml

from foundation import ROOT, CommandRunner, LabConfig, LifecycleError


class ActionClass(str, Enum):
    AUTO_REPAIR = "AUTO_REPAIR"
    APPROVAL_REQUIRED = "APPROVAL_REQUIRED"
    FAIL_CLOSED = "FAIL_CLOSED"


ACTION_POLICY = {
    "terraform_init": ActionClass.AUTO_REPAIR,
    "isolated_kubeconfig_rebuild": ActionClass.AUTO_REPAIR,
    "argocd_local_tunnel": ActionClass.AUTO_REPAIR,
    "kubernetes_mutation": ActionClass.APPROVAL_REQUIRED,
    "gitops_sync": ActionClass.APPROVAL_REQUIRED,
    "secret_mutation": ActionClass.APPROVAL_REQUIRED,
    "database_restore": ActionClass.APPROVAL_REQUIRED,
    "terraform_apply_destroy": ActionClass.APPROVAL_REQUIRED,
    "ambiguous_identity": ActionClass.FAIL_CLOSED,
    "conflicting_ownership": ActionClass.FAIL_CLOSED,
    "uncertain_credentials": ActionClass.FAIL_CLOSED,
}


@dataclass(frozen=True)
class BootstrapIdentity:
    account_id: str
    region: str
    cluster_arn: str
    terraform_workspace: str
    terraform_lineage: str
    terraform_serial: int
    source_revision: str
    evidence_root: str
    kubeconfig: str
    argocd_cli: str
    backend_fingerprint: str

    def checkpoint_identity(self) -> dict[str, str | int]:
        return {
            "account_id": self.account_id,
            "region": self.region,
            "cluster_arn": self.cluster_arn,
            "terraform_workspace": self.terraform_workspace,
            "terraform_lineage": self.terraform_lineage,
            "terraform_serial": self.terraform_serial,
            "source_revision": self.source_revision,
            "evidence_root": self.evidence_root,
        }


class RecoveryBootstrap:
    """Validate recovery identity and repair only local execution plumbing."""

    def __init__(
        self,
        config: LabConfig,
        runner: CommandRunner | None = None,
        *,
        popen_factory: Callable[..., subprocess.Popen] = subprocess.Popen,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self.config = config
        self.runner = runner or CommandRunner(config)
        self.popen_factory = popen_factory
        self.sleep = sleep
        self._owned_tunnel: subprocess.Popen | None = None
        self._argocd_cli: Path | None = None
        self.verified_server: str | None = None
        self._session_token: str | None = None

    def _capture(self, args, *, timeout=120, env=None) -> str:
        return self.runner.run(args, timeout=timeout, env=env).stdout.strip()

    @staticmethod
    def action_class(operation: str) -> ActionClass:
        try:
            return ACTION_POLICY[operation]
        except KeyError as exc:
            raise LifecycleError("Unknown recovery operation classification") from exc

    def _json(self, args, *, timeout=120, env=None) -> dict:
        try:
            value = json.loads(self._capture(args, timeout=timeout, env=env))
        except json.JSONDecodeError as exc:
            raise LifecycleError(f"Invalid JSON from {args[0]}") from exc
        if not isinstance(value, dict):
            raise LifecycleError(f"Expected JSON object from {args[0]}")
        return value

    def _tool(self, name: str) -> Path:
        value = shutil.which(name, path=self.config.environment().get("PATH"))
        if not value:
            raise LifecycleError(f"Required CLI unavailable: {name}")
        path = Path(value).resolve()
        if not path.is_file() or not os.access(path, os.X_OK):
            raise LifecycleError(f"Required CLI is not executable: {name}")
        if path.parent.stat().st_mode & stat.S_IWOTH:
            raise LifecycleError(f"Required CLI is in a world-writable directory: {name}")
        return path

    def _argocd_tool(self) -> Path:
        """Resolve an approved CLI; never download or install an executable."""
        configured = os.getenv("LAB_ARGOCD_CLI")
        if configured:
            candidates = [Path(configured).expanduser()]
        else:
            located = shutil.which("argocd", path=self.config.environment().get("PATH"))
            candidates = [Path(located)] if located else []
            # Historical CLI copies are private evidence, not executable search paths.
            for root in self._evidence_candidates():
                candidates.extend(root.glob("*/argocd"))
                candidates.extend(root.glob("*/bin/argocd"))

        verified: dict[str, list[Path]] = collections.defaultdict(list)
        for raw in candidates:
            if raw.is_symlink() or raw.name != "argocd":
                continue
            path = raw.resolve()
            if not path.is_file() or not os.access(path, os.X_OK):
                continue
            if path.parent.stat().st_mode & stat.S_IWOTH:
                continue
            try:
                digest = hashlib.file_digest(path.open("rb"), "sha256").hexdigest()
                result = subprocess.run(
                    [str(path), "version", "--client", "--short"],
                    capture_output=True, text=True, timeout=10, check=True,
                )
                if "argocd:" not in result.stdout:
                    continue
            except (OSError, subprocess.SubprocessError):
                continue
            verified[digest].append(path)
        if not verified:
            raise LifecycleError("Approved Argo CD CLI unavailable")
        # An explicitly supplied CLI is a separate operator approval.
        if configured:
            if len(verified) != 1:
                raise LifecycleError("Configured Argo CD CLI identity is ambiguous")
            selected = verified[next(iter(verified))][0]
        else:
            # A majority of identical historical copies can establish identity.
            ranked = sorted(verified.items(), key=lambda pair: -len(pair[1]))
            if len(ranked[0][1]) < 2 or (
                len(ranked) > 1 and len(ranked[0][1]) == len(ranked[1][1])
            ):
                raise LifecycleError("Argo CD CLI identity ambiguous; explicit approval required")
            selected = sorted(ranked[0][1])[0]
        self._argocd_cli = selected
        return selected

    def _evidence_candidates(self) -> list[Path]:
        roots = [self.config.evidence_dir]
        # Only inspect the operator's existing local project worktrees.
        projects = Path.home() / "projects"
        if projects.is_dir():
            roots.extend(projects.glob("aws-eks-*/.private-evidence/lifecycle"))
        return sorted({root.resolve() for root in roots if root.is_dir()})

    def _approved_backend_config(self) -> dict:
        """Discover backend metadata from an existing initialized Git worktree."""
        try:
            worktrees = subprocess.check_output(
                ["git", "worktree", "list", "--porcelain"],
                cwd=ROOT, text=True, timeout=15,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise LifecycleError("Cannot discover approved initialized worktrees") from exc
        configs = {}
        for line in worktrees.splitlines():
            if not line.startswith("worktree "):
                continue
            root = Path(line[9:])
            metadata = root / "infra/terraform/environments/lab/.terraform/terraform.tfstate"
            if not metadata.is_file() or metadata.is_symlink():
                continue
            try:
                backend = json.loads(metadata.read_text())["backend"]
                config = backend["config"]
                if backend["type"] != "s3" or not all(config.get(k) for k in ("bucket", "key", "region")):
                    continue
                if config["region"] != self.config.aws_region:
                    continue
                identity = tuple(str(config[k]) for k in ("bucket", "key", "region"))
                configs[identity] = configs.get(identity, 0) + 1
            except (OSError, KeyError, TypeError, ValueError):
                continue
        if len(configs) != 1:
            raise LifecycleError("Approved Terraform backend identity is unavailable or ambiguous")
        bucket, key, region = next(iter(configs))
        return {"bucket": bucket, "key": key, "region": region}

    def _terraform_backend(self, *, auto_repair: bool) -> dict:
        terraform = self._tool("terraform")
        metadata = self.config.terraform_dir / ".terraform" / "terraform.tfstate"
        if metadata.is_symlink():
            raise LifecycleError("Terraform backend metadata cannot be a symlink")
        if not metadata.is_file():
            if not auto_repair:
                raise LifecycleError("Terraform backend is not initialized")
            approved = self._approved_backend_config()
            # Validate tracked backend settings independently of local metadata.
            try:
                backend_hcl = (
                    self.config.terraform_dir / "backend.tf"
                ).read_text(encoding="utf-8")
            except OSError as exc:
                raise LifecycleError(
                    "Approved S3 backend declaration unavailable"
                ) from exc
            if 'backend "s3"' not in backend_hcl:
                raise LifecycleError("Approved S3 backend declaration unavailable")
            for field in ("key", "region"):
                if not re.search(
                    rf'\b{field}\s*=\s*"{re.escape(approved[field])}"', backend_hcl
                ):
                    raise LifecycleError("Approved backend configuration differs from tracked source")
            fd, name = tempfile.mkstemp(prefix=".approved-backend-", suffix=".tfbackend")
            try:
                with os.fdopen(fd, "w") as handle:
                    for field in ("bucket", "key", "region"):
                        handle.write(f"{field} = {json.dumps(approved[field])}\n")
                os.chmod(name, 0o600)
                print("AUTO_REPAIR: initializing verified Terraform backend", flush=True)
                self.runner.run([
                    terraform, f"-chdir={self.config.terraform_dir}", "init",
                    "-input=false", "-reconfigure", "-lockfile=readonly",
                    f"-backend-config={name}",
                ], timeout=600)
            finally:
                Path(name).unlink(missing_ok=True)

        try:
            metadata_value = json.loads(metadata.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise LifecycleError("Terraform backend metadata is unreadable") from exc
        backend = metadata_value.get("backend", {})
        config = backend.get("config", {})
        required = ("bucket", "key", "region")
        if backend.get("type") != "s3" or any(not config.get(key) for key in required):
            raise LifecycleError("Terraform backend identity is incomplete")
        approved_region = str(config["region"])
        if approved_region != self.config.aws_region:
            raise LifecycleError("Terraform backend region mismatch")
        public_identity = {
            "type": backend["type"],
            **{key: str(config[key]) for key in required},
        }
        public_identity["fingerprint"] = hashlib.sha256(
            json.dumps(public_identity, sort_keys=True).encode()
        ).hexdigest()
        return public_identity

    def _terraform_identity(self) -> tuple[str, str, int, dict]:
        terraform = self._tool("terraform")
        prefix = [terraform, f"-chdir={self.config.terraform_dir}"]
        workspace = self._capture([*prefix, "workspace", "show"])
        if not workspace or workspace == "<no workspace>":
            raise LifecycleError("Terraform workspace is unavailable")
        state = self._json([*prefix, "state", "pull"], timeout=300)
        lineage = state.get("lineage")
        serial = state.get("serial")
        if not isinstance(lineage, str) or not lineage or not isinstance(serial, int):
            raise LifecycleError("Terraform state identity is unavailable")
        outputs = self._json([*prefix, "output", "-json"])
        values = {
            key: item.get("value") for key, item in outputs.items()
            if isinstance(item, dict)
        }
        return workspace, lineage, serial, values

    def _aws_identity(self) -> tuple[str, dict]:
        self._tool("aws")
        account = self._capture([
            "aws", "sts", "get-caller-identity", "--query", "Account", "--output", "text",
        ], timeout=60)
        if account != self.config.expected_account_id:
            raise LifecycleError("Unexpected AWS account")
        cluster = self._json([
            "aws", "eks", "describe-cluster", "--name", self.config.cluster_name,
            "--region", self.config.aws_region, "--output", "json",
        ], timeout=120).get("cluster", {})
        expected_arn = (
            f"arn:aws:eks:{self.config.aws_region}:{account}:cluster/{self.config.cluster_name}"
        )
        if cluster.get("arn") != expected_arn or cluster.get("status") != "ACTIVE":
            raise LifecycleError("Live EKS identity is not approved and ACTIVE")
        return account, cluster

    def _isolated_kubeconfig(self, cluster: dict | None = None) -> Path:
        value = os.getenv("KUBECONFIG", "")
        if value:
            if os.pathsep in value:
                raise LifecycleError("One isolated KUBECONFIG is required")
            raw = Path(value).expanduser()
            if not raw.is_absolute() or raw.is_symlink():
                raise LifecycleError("Isolated kubeconfig must be an absolute non-symlink")
            path = raw.resolve()
        else:
            if cluster is None:
                raise LifecycleError("Verified EKS identity required for kubeconfig discovery")
            matches = []
            for root in self._evidence_candidates():
                for candidate in root.glob("*/kubeconfig"):
                    if candidate.is_symlink() or not candidate.is_file():
                        continue
                    if stat.S_IMODE(candidate.stat().st_mode) & 0o077:
                        continue
                    if self._kubeconfig_matches(candidate, cluster):
                        matches.append(candidate.resolve())
            if matches:
                # All matching files are validated against the same live EKS identity.
                path = sorted(matches)[0]
            else:
                path = self.config.evidence_dir / "isolated-recovery-kubeconfig"
                if path.is_symlink():
                    raise LifecycleError("Recovery kubeconfig destination is a symlink")
        default = (Path.home() / ".kube" / "config").resolve()
        if path == default:
            raise LifecycleError("Default kubeconfig cannot be used for recovery")
        return path

    def _kubeconfig_matches(self, path: Path, cluster: dict) -> bool:
        """Compare the selected isolated context with verified EKS identity."""
        if not path.is_file() or path.is_symlink():
            return False
        if stat.S_IMODE(path.stat().st_mode) & 0o077:
            return False

        try:
            with path.open("r", encoding="utf-8") as stream:
                config = yaml.safe_load(stream)

            if not isinstance(config, dict):
                return False

            current = config.get("current-context")
            contexts = [
                item["context"]
                for item in config.get("contexts", [])
                if isinstance(item, dict)
                and item.get("name") == current
            ]

            if not current or len(contexts) != 1:
                return False

            selected = contexts[0]
            cluster_name = selected.get("cluster")

            clusters = [
                item["cluster"]
                for item in config.get("clusters", [])
                if isinstance(item, dict)
                and item.get("name") == cluster_name
            ]

            if len(clusters) != 1:
                return False

            live = clusters[0]

            user_name = selected.get("user")
            users = [
                item["user"]
                for item in config.get("users", [])
                if isinstance(item, dict)
                and item.get("name") == user_name
            ]

            if not user_name or len(users) != 1:
                return False

            authentication = users[0]
            if not isinstance(authentication, dict):
                return False

            command = authentication.get("exec", {})
            if not isinstance(command, dict):
                return False

            if command.get("command") != "aws":
                return False

            args = command.get("args", [])
            if not isinstance(args, list):
                return False
            if not all(isinstance(arg, str) for arg in args):
                return False

            # Parse the approved AWS CLI global options and EKS command.
            # AWS-generated kubeconfigs may place --region before eks get-token.
            options = {}
            tokens = []
            allowed_options = {
                "--region", "--cluster-name",
                "--cluster-id", "--output",
            }

            i = 0
            while i < len(args):
                item = args[i]

                if item in allowed_options:
                    if item in options or i + 1 >= len(args):
                        return False
                    value = args[i + 1]
                    if not value or value.startswith("-"):
                        return False
                    options[item] = value
                    i += 2
                    continue

                if item.startswith("-"):
                    return False

                tokens.append(item)
                i += 1

            if tokens != ["eks", "get-token"]:
                return False

            if options.get("--region") != self.config.aws_region:
                return False

            if options.get("--output") != "json":
                return False

            cluster_name = options.get("--cluster-name")
            cluster_id = options.get("--cluster-id")

            if (cluster_name is None) == (cluster_id is None):
                return False

            if cluster_name is not None:
                if cluster_name != self.config.cluster_name:
                    return False
            elif cluster_id != cluster.get("arn"):
                return False

            exec_env = command.get("env", [])
            if not isinstance(exec_env, list):
                return False
            for item in exec_env:
                if not isinstance(item, dict):
                    return False
                if set(item) != {"name", "value"}:
                    return False
                if item["name"] != "AWS_PROFILE":
                    return False
                if not isinstance(item["value"], str) or not item["value"]:
                    return False

            expected_ca = base64.b64decode(
                cluster["certificateAuthority"]["data"],
                validate=True,
            )
            actual_ca = base64.b64decode(
                live["certificate-authority-data"],
                validate=True,
            )

            return (
                bool(selected.get("user"))
                and live.get("server") == cluster.get("endpoint")
                and actual_ca == expected_ca
            )

        except (
            OSError, KeyError, TypeError, ValueError,
            yaml.YAMLError,
        ):
            return False

    def _ensure_kubeconfig(self, cluster: dict, *, auto_repair: bool) -> Path:
        self._tool("kubectl")
        path = self._isolated_kubeconfig(cluster)
        if self._kubeconfig_matches(path, cluster):
            return path
        if not auto_repair:
            raise LifecycleError("Isolated kubeconfig is missing or stale")
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(prefix=".recovery-kubeconfig-", dir=path.parent)
        os.close(fd)
        temp = Path(temp_name)
        temp.unlink()
        print(
            f"{self.action_class('isolated_kubeconfig_rebuild').value}: "
            "reconstructing the isolated kubeconfig",
            flush=True,
        )
        try:
            self.runner.run([
                "aws", "eks", "update-kubeconfig", "--name", self.config.cluster_name,
                "--region", self.config.aws_region, "--alias", "eks-reliability-lab",
                "--kubeconfig", temp,
            ], timeout=120)
            os.chmod(temp, 0o600)
            if not self._kubeconfig_matches(temp, cluster):
                raise LifecycleError("Reconstructed kubeconfig identity mismatch")
            if path.exists():
                archive = path.with_name(path.name + ".stale-" + str(time.time_ns()))
                os.replace(path, archive)
            os.replace(temp, path)
        finally:
            if temp.exists():
                temp.unlink()
        return path

    def prepare(self, *, auto_repair: bool) -> BootstrapIdentity:
        """Perform read-only identity checks plus approved local AUTO_REPAIR."""
        print("INFO: bootstrap: verifying AWS and Terraform identity", flush=True)
        # Authenticate to the approved account before any backend initialization.
        account, cluster = self._aws_identity()
        backend = self._terraform_backend(auto_repair=auto_repair)
        workspace, lineage, serial, outputs = self._terraform_identity()
        expected_arn = cluster["arn"]
        if outputs.get("cluster_name") != self.config.cluster_name:
            raise LifecycleError("Terraform cluster name mismatch")
        if outputs.get("cluster_arn") != expected_arn:
            raise LifecycleError("Terraform and live EKS identity mismatch")
        kubeconfig = self._ensure_kubeconfig(cluster, auto_repair=auto_repair)
        os.environ["KUBECONFIG"] = str(kubeconfig)
        argocd = self._argocd_tool()
        source_revision = self._capture(["git", "rev-parse", "HEAD"], timeout=30)
        if not re.fullmatch(r"[0-9a-f]{40}", source_revision):
            raise LifecycleError("Recovery source revision is unavailable")
        evidence_root = str(self.config.evidence_dir.expanduser().resolve())
        print("PASS: bootstrap identity verified", flush=True)
        return BootstrapIdentity(
            account_id=account,
            region=self.config.aws_region,
            cluster_arn=expected_arn,
            terraform_workspace=workspace,
            terraform_lineage=lineage,
            terraform_serial=serial,
            source_revision=source_revision,
            evidence_root=evidence_root,
            kubeconfig=str(kubeconfig),
            argocd_cli=str(argocd),
            backend_fingerprint=backend["fingerprint"],
        )

    def _argocd_server(self, cli: Path) -> str:
        configured = os.getenv("LAB_ARGOCD_SERVER", "").strip()
        if configured:
            server = configured
        else:
            context_output = self._capture([cli, "context"], timeout=30)
            current = [
                line for line in context_output.splitlines()
                if line.lstrip().startswith("*")
            ]
            if len(current) != 1:
                raise LifecycleError("Authenticated Argo CD context is ambiguous")
            server = current[0]
        matches = set(re.findall(
            r"(?:https?://)?(localhost|127\.0\.0\.1):(\d{2,5})", server
        ))
        if len(matches) != 1:
            raise LifecycleError("Authenticated Argo CD context is not localhost-only")
        host, raw_port = matches.pop()
        port = int(raw_port)
        if port > 65535:
            raise LifecycleError("Invalid Argo CD localhost port")
        return f"{host}:{port}"

    @staticmethod
    def _listening(port: int) -> bool:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                return True
        except OSError:
            return False

    def _argocd_admin_password(self) -> str:
        """Retrieve the Argo CD initial admin password from the approved secret.

        This is the standard Argo CD credential source (Kubernetes secret
        ``argocd-initial-admin-secret``), not a password reset.  The value
        is never logged or returned to callers.
        """
        raw = self._capture([
            "kubectl", "-n", "argocd", "get", "secret",
            "argocd-initial-admin-secret",
            "-o", "jsonpath={.data.password}",
        ], timeout=30, env=self.config.environment())
        if not raw:
            raise LifecycleError("Argo CD initial admin secret is unavailable")
        password = base64.b64decode(raw).decode()
        if not password:
            raise LifecycleError("Argo CD initial admin password is empty")
        return password

    @staticmethod
    def _argocd_auth_failure(exc: LifecycleError) -> bool:
        """Only explicit authentication rejection permits a credential refresh."""
        detail = str(exc).upper()
        return "UNAUTHENTICATED" in detail or "CATEGORY=ARGOCD_AUTH" in detail

    def _argocd_login(self, cli: Path, server: str) -> None:
        """Create an in-memory Argo CD session over authenticated TLS.

        Trust is anchored in the deployed Argo CD server TLS certificate,
        retrieved from the already verified Kubernetes destination. A localhost
        tunnel is not by itself a server identity guarantee.
        """
        password = self._argocd_admin_password()
        cert_secret = self._json([
            "kubectl", "-n", "argocd", "get", "secret", "argocd-secret",
            "-o", "json",
        ], env=self.config.environment())
        try:
            pem = base64.b64decode(cert_secret["data"]["tls.crt"], validate=True).decode("ascii")
            if "-----BEGIN CERTIFICATE-----" not in pem:
                raise ValueError("Invalid certificate")
        except (KeyError, TypeError, ValueError, UnicodeError) as exc:
            raise LifecycleError("ARGOCD_TLS_TRUST_UNAVAILABLE: approved server certificate missing") from None
        # Authenticate the certificate against the Kubernetes Service DNS name,
        # while TCP traffic traverses the localhost-only port-forward.
        context = ssl.create_default_context(cadata=pem)
        expected_name = "argocd-server"
        port = int(server.rsplit(":", 1)[1])
        token = None
        try:
            connection = http.client.HTTPSConnection("127.0.0.1", port, timeout=20, context=context)
            # Override SNI/hostname verification, not the TCP tunnel destination.
            def verified_connect():
                sock = socket.create_connection(("127.0.0.1", port), timeout=20)
                try:
                    connection.sock = context.wrap_socket(sock, server_hostname=expected_name)
                except BaseException:
                    sock.close()
                    raise
            connection.connect = verified_connect
            try:
                payload = json.dumps({"username": "admin", "password": password}).encode()
                connection.request("POST", "/api/v1/session", body=payload,
                                   headers={"Content-Type": "application/json"})
                response = connection.getresponse()
                if response.status != 200:
                    raise LifecycleError("ARGOCD_AUTHENTICATION_FAILED: session API rejected approved credential")
                body = response.read(65537)
                if len(body) > 65536:
                    raise LifecycleError("ARGOCD_AUTHENTICATION_FAILED: oversized session response")
                value = json.loads(body)
                token = value.get("token") if isinstance(value, dict) else None
                if not isinstance(token, str) or not token:
                    raise LifecycleError("ARGOCD_AUTHENTICATION_FAILED: missing session token")
            finally:
                connection.close()
        except (ssl.SSLError, OSError, TimeoutError) as exc:
            raise LifecycleError("ARGOCD_TLS_OR_TRANSPORT_FAILED: verified session unavailable") from None
        except (ValueError, TypeError, json.JSONDecodeError) as exc:
            raise LifecycleError("ARGOCD_AUTHENTICATION_FAILED: malformed session response") from None
        finally:
            del password
        self._session_token = token

    def _verify_argocd_auth(self, cli: Path, server: str) -> None:
        """Verify Application API access, not just account queries.

        ``argocd account get-user-info`` can return exit 0 with an
        expired or server-mismatched token, but ``argocd app list``
        requires a valid token for the exact server address.
        """
        env = self.config.environment()
        if self._session_token:
            env["ARGOCD_AUTH_TOKEN"] = self._session_token
        self.runner.run([
            cli, "account", "get-user-info", "--server", server,
        ], timeout=30, env=env)
        self.runner.run([
            cli, "app", "list", "--server", server,
        ], timeout=30, env=env)
        self.runner.run([
            cli, "app", "get", "platform-root", "-o", "json", "--server", server,
        ], timeout=30, env=env)

    @contextlib.contextmanager
    def argocd_session(self) -> Iterator[str]:
        """Reuse verified localhost context; refresh only on explicit auth rejection."""
        cli = self._argocd_cli or self._argocd_tool()
        server = self._argocd_server(cli)
        port = int(server.rsplit(":", 1)[1])
        previous_path = os.environ.get("PATH")
        os.environ["PATH"] = str(cli.parent) + os.pathsep + (previous_path or "")
        try:
            if self._listening(port):
                try:
                    self._verify_argocd_auth(cli, server)
                    print("PASS: reused authenticated localhost Argo CD session", flush=True)
                except LifecycleError as exc:
                    if not self._argocd_auth_failure(exc):
                        raise
                    print(
                        f"{self.action_class('argocd_local_tunnel').value}: "
                        "refreshing Argo CD credentials for current server",
                        flush=True,
                    )
                    self._argocd_login(cli, server)
                    self._verify_argocd_auth(cli, server)
                    print("PASS: Argo CD credentials refreshed and Application API verified", flush=True)
                self.verified_server = server
                yield server
                return

            print(
                f"{self.action_class('argocd_local_tunnel').value}: "
                "starting bounded localhost Argo CD tunnel",
                flush=True,
            )
            env = self.config.environment()
            service = self._json([
                "kubectl", "-n", "argocd", "get", "service", "argocd-server", "-o", "json"
            ], env=env)
            ports = [entry.get("port") for entry in service.get("spec", {}).get("ports", [])
                     if entry.get("name") in ("https", "server")]
            if len(ports) != 1 or not isinstance(ports[0], int) or not 1 <= ports[0] <= 65535:
                raise LifecycleError("Argo CD Service port is ambiguous")
            process = self.popen_factory([
                "kubectl", "-n", "argocd", "port-forward", "--address", "127.0.0.1",
                "service/argocd-server", f"{port}:{ports[0]}",
            ], cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            self._owned_tunnel = process
            try:
                for retry in range(1, 21):
                    if process.poll() is not None:
                        raise LifecycleError("Argo CD port-forward exited unexpectedly")
                    if self._listening(port):
                        break
                    print(f"INFO: argocd tunnel retry={retry}", flush=True)
                    self.sleep(0.25)
                else:
                    raise LifecycleError("Argo CD port-forward did not become ready")
                # Try existing approved CLI context before any credential refresh.
                try:
                    self._verify_argocd_auth(cli, server)
                except LifecycleError as exc:
                    if not self._argocd_auth_failure(exc):
                        raise
                    self._argocd_login(cli, server)
                    self._verify_argocd_auth(cli, server)
                self.verified_server = server
                yield server
            finally:
                if self._owned_tunnel is process and process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=5)
                self._owned_tunnel = None
        finally:
            self._session_token = None
            if previous_path is None:
                os.environ.pop("PATH", None)
            else:
                os.environ["PATH"] = previous_path
