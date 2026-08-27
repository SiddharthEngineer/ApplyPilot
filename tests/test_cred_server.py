"""Tests for the credential MCP server (cred_server.py)."""

import asyncio
import json
import os
from unittest.mock import AsyncMock, patch

from applypilot.apply.cred_server import (
    ATS_PW_ENV,
    CAPTCHA_TYPE_MAP,
    TOOLS,
    _get_capsolver_key,
    _get_password,
    _get_password_from_profile,
    _handle_message,
    _handle_tool_call,
    _solve_captcha,
)


def _run_async(coro):
    """Helper to run async functions in sync tests."""
    return asyncio.get_event_loop().run_until_complete(coro)


class TestGetPasswordFromProfile:
    """Test profile.json password reading."""

    def test_reads_from_profile_json(self, tmp_path):
        profile = {"site_passwords": {"workday": "secret123", "greenhouse": "gh_pass"}}
        profile_path = tmp_path / "profile.json"
        profile_path.write_text(json.dumps(profile))
        with patch.dict(os.environ, {"APPLYPILOT_APP_DIR": str(tmp_path)}):
            assert _get_password_from_profile("workday") == "secret123"
            assert _get_password_from_profile("greenhouse") == "gh_pass"

    def test_returns_none_when_app_dir_not_set(self):
        env = os.environ.copy()
        env.pop("APPLYPILOT_APP_DIR", None)
        with patch.dict(os.environ, env, clear=True):
            assert _get_password_from_profile("workday") is None

    def test_returns_none_when_profile_missing(self, tmp_path):
        with patch.dict(os.environ, {"APPLYPILOT_APP_DIR": str(tmp_path)}):
            assert _get_password_from_profile("workday") is None

    def test_returns_none_when_malformed_json(self, tmp_path):
        profile_path = tmp_path / "profile.json"
        profile_path.write_text("not valid json {{{")
        with patch.dict(os.environ, {"APPLYPILOT_APP_DIR": str(tmp_path)}):
            assert _get_password_from_profile("workday") is None

    def test_returns_none_when_ats_not_in_profile(self, tmp_path):
        profile = {"site_passwords": {"greenhouse": "gh_pass"}}
        profile_path = tmp_path / "profile.json"
        profile_path.write_text(json.dumps(profile))
        with patch.dict(os.environ, {"APPLYPILOT_APP_DIR": str(tmp_path)}):
            assert _get_password_from_profile("workday") is None

    def test_returns_none_for_empty_password(self, tmp_path):
        profile = {"site_passwords": {"workday": ""}}
        profile_path = tmp_path / "profile.json"
        profile_path.write_text(json.dumps(profile))
        with patch.dict(os.environ, {"APPLYPILOT_APP_DIR": str(tmp_path)}):
            assert _get_password_from_profile("workday") is None


class TestGetPassword:
    """Test env var reading for passwords with profile-first fallback."""

    def test_profile_takes_precedence_over_env(self, tmp_path):
        profile = {"site_passwords": {"workday": "from_profile"}}
        profile_path = tmp_path / "profile.json"
        profile_path.write_text(json.dumps(profile))
        with (
            patch.dict(os.environ, {"APPLYPILOT_APP_DIR": str(tmp_path)}),
            patch.dict(os.environ, {"APPLYPILOT_PW_WORKDAY": "from_env"}),
        ):
            assert _get_password("workday") == "from_profile"

    def test_falls_back_to_env_when_no_profile(self):
        env = os.environ.copy()
        env.pop("APPLYPILOT_APP_DIR", None)
        with (
            patch.dict(os.environ, env, clear=True),
            patch.dict(os.environ, {"APPLYPILOT_PW_WORKDAY": "env_pass"}),
        ):
            assert _get_password("workday") == "env_pass"

    def test_returns_none_when_not_set(self):
        env = os.environ.copy()
        env.pop("APPLYPILOT_APP_DIR", None)
        env.pop("APPLYPILOT_PW_WORKDAY", None)
        with patch.dict(os.environ, env, clear=True):
            assert _get_password("workday") is None

    def test_returns_none_for_empty_string(self):
        env = os.environ.copy()
        env.pop("APPLYPILOT_APP_DIR", None)
        with (
            patch.dict(os.environ, env, clear=True),
            patch.dict(os.environ, {"APPLYPILOT_PW_GREENHOUSE": ""}),
        ):
            assert _get_password("greenhouse") is None

    def test_returns_none_for_unknown_ats(self):
        assert _get_password("unknown_ats") is None

    def test_all_ats_platforms_have_env_vars(self):
        for ats in ("workday", "greenhouse", "lever", "ashby"):
            assert ats in ATS_PW_ENV
            assert ATS_PW_ENV[ats].startswith("APPLYPILOT_PW_")


class TestToolDefinitions:
    """Test MCP tool definitions."""

    def test_tools_list_has_ats_login(self):
        names = [t["name"] for t in TOOLS]
        assert "ats_login" in names

    def test_ats_login_schema_requires_ats_and_email(self):
        ats_login = next(t for t in TOOLS if t["name"] == "ats_login")
        schema = ats_login["inputSchema"]
        assert "ats" in schema["required"]
        assert "email" in schema["required"]

    def test_ats_login_ats_enum_values(self):
        ats_login = next(t for t in TOOLS if t["name"] == "ats_login")
        ats_enum = ats_login["inputSchema"]["properties"]["ats"]["enum"]
        assert set(ats_enum) == {"workday", "greenhouse", "lever", "ashby"}


class TestHandleToolCall:
    """Test tool dispatch and results."""

    def test_unknown_tool_returns_error(self):
        result = _run_async(_handle_tool_call("unknown_tool", {}))
        assert result["isError"] is True
        text = json.loads(result["content"][0]["text"])
        assert text["success"] is False
        assert "unknown_tool" in text["message"]

    def test_unknown_ats_returns_error(self):
        result = _run_async(_handle_tool_call("ats_login", {"ats": "unknown", "email": "x@y.com"}))
        text = json.loads(result["content"][0]["text"])
        assert text["success"] is False
        assert "unknown_ats" in text["message"]

    def test_no_password_returns_no_password_configured(self):
        env = os.environ.copy()
        env.pop("APPLYPILOT_APP_DIR", None)
        for key in ATS_PW_ENV.values():
            env.pop(key, None)
        with patch.dict(os.environ, env, clear=True):
            result = _run_async(_handle_tool_call("ats_login", {
                "ats": "workday",
                "email": "test@example.com",
                "cdp_port": 9222,
            }))
            text = json.loads(result["content"][0]["text"])
            assert text["success"] is False
            assert text["message"] == "no_password_configured"

    def test_cdp_connection_failure(self):
        with (
            patch.dict(os.environ, {"APPLYPILOT_PW_WORKDAY": "pass123"}),
            patch("applypilot.apply.cred_server._fill_login_form", new_callable=AsyncMock) as mock,
        ):
            mock.return_value = {"success": False, "message": "cdp_connection_failed"}
            result = _run_async(_handle_tool_call("ats_login", {
                "ats": "workday",
                "email": "test@example.com",
            }))
            text = json.loads(result["content"][0]["text"])
            assert text["success"] is False
            assert text["message"] == "cdp_connection_failed"


class TestHandleMessage:
    """Test MCP JSON-RPC protocol handling."""

    def test_initialize_returns_capabilities(self):
        msg = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}
        resp = _run_async(_handle_message(msg))
        assert resp["id"] == 1
        assert resp["result"]["protocolVersion"] == "2024-11-05"
        assert "tools" in resp["result"]["capabilities"]

    def test_notifications_initialized_returns_none(self):
        msg = {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}}
        resp = _run_async(_handle_message(msg))
        assert resp is None

    def test_tools_list_returns_tools(self):
        msg = {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}
        resp = _run_async(_handle_message(msg))
        assert resp["id"] == 2
        tool_names = [t["name"] for t in resp["result"]["tools"]]
        assert "ats_login" in tool_names
        assert "captcha_solve" in tool_names

    def test_unknown_method_returns_error(self):
        msg = {"jsonrpc": "2.0", "id": 3, "method": "unknown/method", "params": {}}
        resp = _run_async(_handle_message(msg))
        assert resp["error"]["code"] == -32601

    def test_notification_with_unknown_method_no_response(self):
        msg = {"jsonrpc": "2.0", "method": "unknown/notification"}
        resp = _run_async(_handle_message(msg))
        assert resp is None


class TestCaptchaSolve:
    """Test captcha_solve tool definition and functionality."""

    def test_tool_definition_present(self):
        names = [t["name"] for t in TOOLS]
        assert "captcha_solve" in names

    def test_tool_schema_requires_correct_fields(self):
        captcha_tool = next(t for t in TOOLS if t["name"] == "captcha_solve")
        schema = captcha_tool["inputSchema"]
        assert "captcha_type" in schema["required"]
        assert "website_url" in schema["required"]
        assert "website_key" in schema["required"]

    def test_captcha_type_enum_values(self):
        captcha_tool = next(t for t in TOOLS if t["name"] == "captcha_solve")
        enum = captcha_tool["inputSchema"]["properties"]["captcha_type"]["enum"]
        assert set(enum) == {
            "hcaptcha",
            "recaptchav2",
            "recaptchav3",
            "turnstile",
            "funcaptcha",
        }

    def test_missing_key_returns_no_capsolver_key_configured(self):
        env = os.environ.copy()
        env.pop("CAPSOLVER_API_KEY", None)
        with patch.dict(os.environ, env, clear=True):
            result = _run_async(_solve_captcha(
                "hcaptcha", "https://example.com", "sitekey123",
            ))
            assert result["success"] is False
            assert result["message"] == "no_capsolver_key_configured"

    def test_unsupported_captcha_type(self):
        result = _run_async(_solve_captcha(
            "unknown_type", "https://example.com", "key",
        ))
        assert result["success"] is False
        assert "unsupported_captcha_type" in result["message"]

    def test_successful_solve_hcaptcha(self):
        create_response = {"errorId": 0, "taskId": "task_abc123"}
        result_response = {
            "errorId": 0,
            "status": "ready",
            "solution": {"gRecaptchaResponse": "SOLVED_TOKEN_xyz"},
        }

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        # First call is createTask, second is getTaskResult
        mock_client.post = AsyncMock(side_effect=[
            AsyncMock(json=lambda: create_response),
            AsyncMock(json=lambda: result_response),
        ])

        with (
            patch.dict(os.environ, {"CAPSOLVER_API_KEY": "test_key_123"}),
            patch("applypilot.apply.cred_server._httpx") as mock_httpx,
        ):
            mock_httpx.AsyncClient.return_value = mock_client
            mock_httpx.HTTPError = Exception

            result = _run_async(_solve_captcha(
                "hcaptcha", "https://example.com", "sitekey123",
            ))
            assert result["success"] is True
            assert result["token"] == "SOLVED_TOKEN_xyz"

    def test_successful_solve_turnstile(self):
        create_response = {"errorId": 0, "taskId": "task_ts1"}
        result_response = {
            "errorId": 0,
            "status": "ready",
            "solution": {"token": "TURNSTILE_TOKEN_abc"},
        }

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(side_effect=[
            AsyncMock(json=lambda: create_response),
            AsyncMock(json=lambda: result_response),
        ])

        with (
            patch.dict(os.environ, {"CAPSOLVER_API_KEY": "test_key_123"}),
            patch("applypilot.apply.cred_server._httpx") as mock_httpx,
        ):
            mock_httpx.AsyncClient.return_value = mock_client
            mock_httpx.HTTPError = Exception

            result = _run_async(_solve_captcha(
                "turnstile", "https://example.com", "ts_sitekey",
            ))
            assert result["success"] is True
            assert result["token"] == "TURNSTILE_TOKEN_abc"

    def test_capsolver_error_returns_failure(self):
        error_response = {
            "errorId": 12,
            "errorDescription": "ERROR sitekey is invalid",
        }

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=AsyncMock(json=lambda: error_response))

        with (
            patch.dict(os.environ, {"CAPSOLVER_API_KEY": "test_key_123"}),
            patch("applypilot.apply.cred_server._httpx") as mock_httpx,
        ):
            mock_httpx.AsyncClient.return_value = mock_client
            mock_httpx.HTTPError = Exception

            result = _run_async(_solve_captcha(
                "hcaptcha", "https://example.com", "bad_key",
            ))
            assert result["success"] is False
            assert "createTask_failed" in result["message"]

    def test_result_never_leaks_key(self):
        create_response = {"errorId": 0, "taskId": "task_xyz"}
        result_response = {
            "errorId": 0,
            "status": "ready",
            "solution": {"gRecaptchaResponse": "TOK"},
        }

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(side_effect=[
            AsyncMock(json=lambda: create_response),
            AsyncMock(json=lambda: result_response),
        ])

        fake_key = "CAP_super_secret_key_abc123"
        with (
            patch.dict(os.environ, {"CAPSOLVER_API_KEY": fake_key}),
            patch("applypilot.apply.cred_server._httpx") as mock_httpx,
        ):
            mock_httpx.AsyncClient.return_value = mock_client
            mock_httpx.HTTPError = Exception

            result = _run_async(_solve_captcha(
                "hcaptcha", "https://example.com", "sitekey123",
            ))
            result_json = json.dumps(result)
            assert fake_key not in result_json

    def test_get_capsolver_key_reads_env(self):
        with patch.dict(os.environ, {"CAPSOLVER_API_KEY": "my_test_key"}):
            assert _get_capsolver_key() == "my_test_key"

    def test_get_capsolver_key_returns_none_when_unset(self):
        env = os.environ.copy()
        env.pop("CAPSOLVER_API_KEY", None)
        with patch.dict(os.environ, env, clear=True):
            assert _get_capsolver_key() is None

    def test_get_capsolver_key_returns_none_when_empty(self):
        with patch.dict(os.environ, {"CAPSOLVER_API_KEY": ""}):
            assert _get_capsolver_key() is None

    def test_captcha_type_map_all_values_cap_solver_format(self):
        expected = {
            "hcaptcha": "HCaptchaTaskProxyLess",
            "recaptchav2": "ReCaptchaV2TaskProxyLess",
            "recaptchav3": "ReCaptchaV3TaskProxyLess",
            "turnstile": "AntiTurnstileTaskProxyLess",
            "funcaptcha": "FunCaptchaTaskProxyLess",
        }
        assert CAPTCHA_TYPE_MAP == expected

    def test_handle_tool_call_dispatches_captcha_solve(self):
        with (
            patch.dict(os.environ, {"CAPSOLVER_API_KEY": "test_key"}),
            patch("applypilot.apply.cred_server._solve_captcha", new_callable=AsyncMock) as mock,
        ):
            mock.return_value = {"success": True, "token": "TOK"}
            result = _run_async(_handle_tool_call("captcha_solve", {
                "captcha_type": "hcaptcha",
                "website_url": "https://example.com",
                "website_key": "key123",
            }))
            text = json.loads(result["content"][0]["text"])
            assert text["success"] is True
            assert text["token"] == "TOK"
            mock.assert_called_once_with(
                "hcaptcha", "https://example.com", "key123", None, None,
            )

    def test_handle_tool_call_rejects_unsupported_type(self):
        result = _run_async(_handle_tool_call("captcha_solve", {
            "captcha_type": "bad_type",
            "website_url": "https://example.com",
            "website_key": "key123",
        }))
        text = json.loads(result["content"][0]["text"])
        assert text["success"] is False
        assert "unsupported_captcha_type" in text["message"]
