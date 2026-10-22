"""Focused regression tests for native database restore diagnostics."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts/lifecycle"))

import labctl
from foundation import LifecycleError


def test_controlled_restore_failure_reports_operation_and_exit_code():
    failure = LifecycleError(
        "Command failed: kubectl (exit=1; category=UNCLASSIFIED; "
        "private stderr suppressed)"
    )
    message = labctl.blocked_error_message(
        failure, operation="native-database-restore"
    )
    assert "stage=native-database-restore" in message
    assert "category=LifecycleError" in message
    assert "Command failed: kubectl" in message
    assert "exit=1" in message


def test_restore_diagnostic_redacts_credentials_and_tokens():
    failure = LifecycleError(
        "password=hunter2 token=abc123 "
        "connection_string=postgresql://app:db-password-7@db/app"
    )
    message = labctl.blocked_error_message(
        failure, operation="native-database-restore"
    )
    assert "password=[REDACTED]" in message
    assert "token=[REDACTED]" in message
    assert "hunter2" not in message
    assert "abc123" not in message
    assert "db-password-7" not in message
    assert "postgresql://" not in message


def test_restore_diagnostic_preserves_sanitized_chained_cause():
    cause = LifecycleError(
        "Command failed: pg_restore (exit=1; token=do-not-print)"
    )
    outer = LifecycleError(
        "Database restore failed and temporary-resource cleanup is incomplete"
    )
    outer.__cause__ = cause
    message = labctl.blocked_error_message(
        outer, operation="native-database-restore"
    )
    assert "temporary-resource cleanup is incomplete" in message
    assert "cause=Command failed: pg_restore (exit=1;" in message
    assert "do-not-print" not in message


def test_uncontrolled_exception_detail_remains_suppressed():
    message = labctl.blocked_error_message(
        ValueError("token=do-not-print"),
        operation="native-database-restore",
    )
    assert "do-not-print" not in message
    assert "unexpected failure; exception detail suppressed" in message
