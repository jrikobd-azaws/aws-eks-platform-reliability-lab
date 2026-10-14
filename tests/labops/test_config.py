import pytest

from scripts.labops.config import ConfigError, load_config


def test_missing_required_config_raises():
    with pytest.raises(ConfigError, match="EXPECTED_ACCOUNT_ID"):
        load_config({"EXPECTED_AWS_REGION": "us-east-1"}, env={})


def test_invalid_account_id_raises():
    with pytest.raises(ConfigError, match="12-digit"):
        load_config({
            "EXPECTED_ACCOUNT_ID": "INVALID",
            "EXPECTED_AWS_REGION": "us-east-1",
        }, env={})


def test_invalid_region_raises():
    with pytest.raises(ConfigError, match="AWS region"):
        load_config({
            "EXPECTED_ACCOUNT_ID": "123456789012",
            "EXPECTED_AWS_REGION": "not-a-region",
        }, env={})


def test_valid_config_passes():
    cfg = load_config({
        "EXPECTED_ACCOUNT_ID": "123456789012",
        "EXPECTED_AWS_REGION": "us-east-1",
        "AWS_PROFILE": "lab-readonly",
    }, env={})

    assert cfg["EXPECTED_ACCOUNT_ID"] == "123456789012"
    assert cfg["EXPECTED_AWS_REGION"] == "us-east-1"
    assert cfg["AWS_PROFILE"] == "lab-readonly"
