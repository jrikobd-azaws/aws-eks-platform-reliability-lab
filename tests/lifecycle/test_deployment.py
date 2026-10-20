"""New-account deployment safety and portability tests."""

import json
import sys
from pathlib import Path
from unittest.mock import Mock

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts/lifecycle"))

from deployment import (
    DeploymentConfig,
    build_deployment_plan,
    validate_deployment_config,
    validate_target_account,
)
from foundation import LifecycleError


def example():
    path = ROOT / "config/lab.example.json"
    data = json.loads(path.read_text())
    data["aws"]["profile"] = "test-profile"
    data["terraform"]["backend"]["bucket"] = "test-lab-state-123456789012"
    return DeploymentConfig(raw=data, path=path)


def test_valid_portable_configuration():
    validate_deployment_config(example())


def test_fresh_mode_required():
    c = example()
    c.raw["deployment_mode"] = "recovery"
    with pytest.raises(LifecycleError):
        validate_deployment_config(c)


def test_account_id_validation():
    c = example()
    c.raw["aws"]["expected_account_id"] = "wrong"
    with pytest.raises(LifecycleError):
        validate_deployment_config(c)


def test_backend_placeholder_rejected():
    c = example()
    c.raw["terraform"]["backend"]["bucket"] = "REPLACE_BUCKET"
    with pytest.raises(LifecycleError):
        validate_deployment_config(c)


def test_private_backup_rejected():
    c = example()
    c.raw["database"]["restore_existing_backup"] = True
    with pytest.raises(LifecycleError):
        validate_deployment_config(c)


def test_cross_account_rejected():
    c = example()
    runner = Mock()
    runner.run.return_value.stdout = "999999999999"

    with pytest.raises(LifecycleError):
        validate_target_account(c, runner)


def test_deployment_plan_is_non_destructive():
    plan = build_deployment_plan(example())

    assert plan["mode"] == "fresh"
    assert plan["aws_resources_modified"] is False
    assert "bootstrap_state_backend" in plan["stages"]


def test_secrets_must_be_new():
    c = example()
    c.raw["secrets"]["app_runtime"]["source"] = "existing"

    with pytest.raises(LifecycleError):
        validate_deployment_config(c)


def test_images_must_be_built():
    c = example()
    c.raw["images"]["backend"]["source"] = "existing"

    with pytest.raises(LifecycleError):
        validate_deployment_config(c)


def test_gitops_paths_remain_inside_repo():
    c = example()
    c.raw["gitops"]["bootstrap_path"] = "../../outside"

    with pytest.raises(LifecycleError):
        validate_deployment_config(c)
