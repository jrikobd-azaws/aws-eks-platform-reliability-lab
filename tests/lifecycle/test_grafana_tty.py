"""PTY and integration regressions for recovery operator reliability."""

import builtins
import os
import pty
import select
import signal
import subprocess
import sys
import time
from pathlib import Path
from unittest.mock import Mock

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts/lifecycle"))

from foundation import CheckpointStore, LabConfig, LifecycleError
from lifecycle import call
from recover_platform import PlatformRecovery, sanitized_failure


def recovery(tmp_path):
    return PlatformRecovery(
        LabConfig(evidence_dir=tmp_path),
        CheckpointStore(tmp_path / "platform-checkpoint.json"),
        executor=Mock(return_value=b""),
    )


def pty_credentials(tmp_path, responses):
    """Exercise the real /dev/tty path with a strict, cleaned-up deadline."""
    pid, master = pty.fork()
    if pid == 0:
        try:
            username, password = recovery(tmp_path)._grafana_credentials()
            os.write(1, f"RESULT:{username}:{len(password)}\n".encode())
        except Exception as exc:
            os.write(1, (sanitized_failure(exc) + "\n").encode())
        finally:
            os._exit(0)

    transcript = bytearray()
    prompts = (
        b"Grafana admin user [admin]: ",
        b"Grafana admin password: ",
        b"Confirm Grafana admin password: ",
    )
    sent = 0
    deadline = time.monotonic() + 8
    try:
        while time.monotonic() < deadline:
            readable, _, _ = select.select([master], [], [], 0.1)
            if readable:
                try:
                    chunk = os.read(master, 4096)
                except OSError:
                    chunk = b""
                if chunk:
                    transcript.extend(chunk)
            if sent < len(prompts) and prompts[sent] in transcript:
                os.write(master, responses[sent] + b"\n")
                sent += 1
            waited, _ = os.waitpid(pid, os.WNOHANG)
            if waited == pid:
                return bytes(transcript)
        raise AssertionError("PTY credential regression timed out")
    finally:
        try:
            os.close(master)
        except OSError:
            pass
        try:
            waited, _ = os.waitpid(pid, os.WNOHANG)
            if waited == 0:
                os.kill(pid, signal.SIGKILL)
                os.waitpid(pid, 0)
        except ChildProcessError:
            pass


def test_grafana_tty_success_is_non_echoing(tmp_path):
    password = b"correct-horse-battery-staple"
    transcript = pty_credentials(tmp_path, (b"", password, password))
    assert b"RESULT:admin:28" in transcript
    assert password not in transcript


def test_grafana_tty_confirmation_mismatch(tmp_path):
    first = b"correct-horse-battery-staple"
    second = b"different-secure-password"
    transcript = pty_credentials(tmp_path, (b"admin", first, second))
    assert b"Grafana passwords do not match" in transcript
    assert first not in transcript
    assert second not in transcript


def test_grafana_tty_rejects_short_password(tmp_path):
    password = b"too-short"
    transcript = pty_credentials(tmp_path, (b"admin", password, password))
    assert b"at least 16 characters" in transcript
    assert password not in transcript


def test_grafana_tty_unavailable_fails_closed(tmp_path, monkeypatch):
    real_open = builtins.open

    def unavailable(path, *args, **kwargs):
        if path == "/dev/tty":
            raise OSError("no controlling terminal")
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", unavailable)
    with pytest.raises(LifecycleError, match="Interactive terminal required"):
        recovery(tmp_path)._grafana_credentials()


def test_lifecycle_call_reports_exit_without_stderr(monkeypatch):
    error = subprocess.CalledProcessError(
        23, ["kubectl", "get", "pods"], stderr=b"password=do-not-emit"
    )
    monkeypatch.setattr(subprocess, "run", Mock(side_effect=error))
    with pytest.raises(LifecycleError, match=r"kubectl \(exit=23") as exc:
        call(["kubectl", "get", "pods"])
    assert "do-not-emit" not in str(exc.value)


def test_sanitized_failure_redacts_sensitive_assignments():
    message = sanitized_failure(
        LifecycleError("login failed password=private token:secret")
    )
    assert "private" not in message
    assert "secret" not in message.lower().split("token=")[-1]
    assert "[REDACTED]" in message


def test_environment_preserves_home_profile_and_kubeconfig(monkeypatch):
    monkeypatch.setenv("HOME", "/home/operator")
    monkeypatch.setenv("AWS_CONFIG_FILE", "/home/operator/.aws/config")
    monkeypatch.setenv("KUBECONFIG", "/home/operator/.kube/recovery")
    env = LabConfig(aws_profile="approved-profile").environment()
    assert env["HOME"] == "/home/operator"
    assert env["AWS_CONFIG_FILE"] == "/home/operator/.aws/config"
    assert env["KUBECONFIG"] == "/home/operator/.kube/recovery"
    assert env["AWS_PROFILE"] == "approved-profile"
