"""Tests for launcher.py config builders and password handling."""

import os
import sys
from pathlib import Path
from unittest.mock import patch

from applypilot.apply.launcher import (
    _build_opencode_cmd,
    _make_mcp_config,
    _make_opencode_config,
)


class TestMakeMcpConfig:
    """Test MCP config generation with cred-server."""

    def test_includes_cred_server(self):
        config = _make_mcp_config(9222)
        assert "cred" in config["mcpServers"]
        cred = config["mcpServers"]["cred"]
        assert cred["command"] == sys.executable
        assert "cred_server.py" in cred["args"][0]

    def test_cred_server_has_app_dir_and_capsolver(self):
        config = _make_mcp_config(9222)
        env = config["mcpServers"]["cred"]["env"]
        assert "APPLYPILOT_APP_DIR" in env
        assert "CAPSOLVER_API_KEY" in env

    def test_no_passwords_in_mcp_config(self):
        config = _make_mcp_config(9222)
        env = config["mcpServers"]["cred"]["env"]
        for key in env:
            assert not key.startswith("APPLYPILOT_PW_"), f"Unexpected password key: {key}"

    def test_capsolver_key_from_env(self):
        with patch.dict(os.environ, {"CAPSOLVER_API_KEY": "test_key_123"}):
            config = _make_mcp_config(9222)
            assert config["mcpServers"]["cred"]["env"]["CAPSOLVER_API_KEY"] == "test_key_123"

    def test_capsolver_key_empty_when_not_set(self):
        env = os.environ.copy()
        env.pop("CAPSOLVER_API_KEY", None)
        with patch.dict(os.environ, env, clear=True):
            config = _make_mcp_config(9222)
            assert config["mcpServers"]["cred"]["env"]["CAPSOLVER_API_KEY"] == ""

    def test_playwright_server_unchanged(self):
        config = _make_mcp_config(9333)
        pw = config["mcpServers"]["playwright"]
        assert "9333" in pw["args"][1]
        assert "@playwright/mcp@latest" in pw["args"][0]

    def test_gmail_server_unchanged(self):
        config = _make_mcp_config(9222)
        assert "gmail" in config["mcpServers"]

    def test_app_dir_points_to_config_app_dir(self):
        from applypilot import config
        config_result = _make_mcp_config(9222)
        env = config_result["mcpServers"]["cred"]["env"]
        assert env["APPLYPILOT_APP_DIR"] == str(config.APP_DIR)


class TestMakeOpencodeConfig:
    """Test OpenCode config generation with cred-server."""

    def test_includes_cred_server(self):
        config = _make_opencode_config(9222)
        assert "cred" in config["mcpServers"]

    def test_no_passwords_in_opencode_config(self):
        config = _make_opencode_config(9222)
        env = config["mcpServers"]["cred"]["env"]
        for key in env:
            assert not key.startswith("APPLYPILOT_PW_"), f"Unexpected password key: {key}"

    def test_permission_rules_include_cred(self):
        config = _make_opencode_config(9222)
        perm = config["permission"]
        assert perm["ats_login"] == "allow"
        assert perm["captcha_solve"] == "allow"
        assert perm["playwright_*"] == "allow"
        assert perm["gmail_*"] == "deny"


class TestBuildOpencodeCmd:
    """Test OpenCode command builder."""

    def test_no_prompt_in_args(self):
        cmd = _build_opencode_cmd("sonnet", Path("/tmp/worker"))
        assert all("password" not in arg.lower() for arg in cmd)
        assert "--model" in cmd
        assert "sonnet" in cmd

    def test_cmd_structure(self):
        cmd = _build_opencode_cmd("gpt-4o", Path("/tmp/w"))
        assert cmd[0] == "opencode"
        assert cmd[1] == "run"
        assert "--auto" in cmd
        assert "--format" in cmd
        assert "json" in cmd
