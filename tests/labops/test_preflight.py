from unittest.mock import Mock

import pytest
from botocore.exceptions import ClientError

from scripts.labops.preflight import PreflightError, run_preflight
from scripts.labops.config import load_config


@pytest.fixture
def valid_config():
    return load_config({
        "EXPECTED_ACCOUNT_ID": "123456789012",
        "EXPECTED_AWS_REGION": "us-east-1",
        "AWS_PROFILE": "lab-readonly",
    }, env={})


def test_preview_mode_makes_no_external_calls(valid_config):
    session_factory = Mock()

    result = run_preflight(valid_config, mode="preview", session_factory=session_factory)

    assert result["success"] is True
    assert result["mode"] == "preview"
    assert session_factory.call_count == 0


def test_wrong_account_is_rejected(valid_config):
    mock_session = Mock()
    mock_session.region_name = "us-east-1"
    mock_session.client.return_value.get_caller_identity.return_value = {"Account": "999999999999"}

    with pytest.raises(PreflightError, match="Expected AWS account"):
        run_preflight(valid_config, mode="check", session_factory=lambda: mock_session)


def test_wrong_region_is_rejected(valid_config):
    mock_session = Mock()
    mock_session.region_name = "us-west-2"
    mock_session.client.return_value.get_caller_identity.return_value = {"Account": "123456789012"}

    with pytest.raises(PreflightError, match="Expected AWS session region"):
        run_preflight(valid_config, mode="check", session_factory=lambda: mock_session)


def test_failed_api_response_is_rejected(valid_config):
    mock_session = Mock()
    mock_session.region_name = "us-east-1"
    mock_session.client.return_value.get_caller_identity.side_effect = ClientError(
        {"Error": {"Code": "AccessDenied", "Message": "denied"}},
        "GetCallerIdentity",
    )

    with pytest.raises(PreflightError, match="Failed to read AWS identity"):
        run_preflight(valid_config, mode="check", session_factory=lambda: mock_session)


def test_check_mode_accepts_matching_identity(valid_config):
    mock_session = Mock()
    mock_session.region_name = "us-east-1"
    mock_session.client.return_value.get_caller_identity.return_value = {"Account": "123456789012"}

    result = run_preflight(valid_config, mode="check", session_factory=lambda: mock_session)

    assert result["success"] is True
    assert result["account"] == "123456789012"
    assert result["region"] == "us-east-1"
