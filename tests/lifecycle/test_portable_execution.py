
import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts/lifecycle"))

from deployment import portable_deploy
from foundation import LifecycleError


def configuration(tmp_path):
    data = json.loads(
        (ROOT / "config/lab.example.json").read_text()
    )
    data["aws"]["profile"] = "test-profile"
    data["aws"]["expected_account_id"] = "123456789012"
    data["terraform"]["backend"]["bucket"] = (
        "portable-lab-state-123456789012"
    )
    path = tmp_path / "lab.json"
    path.write_text(json.dumps(data))
    return path


def test_offline_preflight(tmp_path):
    config = configuration(tmp_path)

    with patch("deployment._deployment_identity") as identity:
        result = portable_deploy(config, stage="preflight")

    identity.assert_not_called()
    assert result["mode"] == "fresh"


def test_mutation_requires_explicit_confirmation(tmp_path):
    config = configuration(tmp_path)

    with pytest.raises(LifecycleError):
        portable_deploy(config, stage="backend")


def test_wrong_confirmation_rejected(tmp_path):
    config = configuration(tmp_path)

    with pytest.raises(LifecycleError):
        portable_deploy(
            config,
            stage="ecr",
            confirm="DEPLOY-WRONG-ACCOUNT",
        )


def test_unknown_stage_rejected(tmp_path):
    config = configuration(tmp_path)

    with pytest.raises(LifecycleError):
        portable_deploy(
            config,
            stage="unknown",
            confirm="anything",
        )


def test_existing_account_backend_rejected(tmp_path):
    config = configuration(tmp_path)
    data = json.loads(config.read_text())
    data["terraform"]["backend"]["bucket"] = (
        "azawslab-eks-tfstate-275544662748-eu-west-1"
    )
    config.write_text(json.dumps(data))

    with pytest.raises(LifecycleError):
        portable_deploy(config, stage="preflight")


def test_unreviewed_terraform_plan_blocked(tmp_path):
    config = configuration(tmp_path)

    with patch(
        "deployment._deployment_identity",
        return_value={},
    ):
        with pytest.raises(LifecycleError):
            portable_deploy(
                config,
                stage="terraform_plan",
                confirm="DEPLOY-123456789012-TERRAFORM_PLAN",
            )


def test_no_implicit_apply_in_deployment():
    source = (
        ROOT / "scripts/lifecycle/deployment.py"
    ).read_text()

    assert "terraform apply" not in source
    assert "terraform destroy" not in source


def test_private_recovery_archive_not_in_template():
    source = (
        ROOT / "config/lab.example.json"
    ).read_text()

    assert ".private-evidence" not in source
    assert "rds!db-" not in source
