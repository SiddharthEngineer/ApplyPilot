"""Tests for `strelitzia doctor`: the server checks (database, sign-in and Drive keys, Claude)."""

from __future__ import annotations

from unittest.mock import patch



class TestDoctorClaude:
    """The Claude block in doctor: key presence (never the value) and the model and effort per stage."""

    def _invoke(self, tmp_path, monkeypatch, env):
        from typer.testing import CliRunner

        from strelitzia.cli import app

        for key in ("ANTHROPIC_API_KEY", *(f"{prefix}_{name}" for prefix in ("STRELITZIA", "APPLYPILOT") for name in
                    ("GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "SESSION_SECRET", "OWNER_EMAIL", "PUBLIC_URL",
                     "TOKEN_KEY")), "GEMINI_API_KEY", "LLM_MONTHLY_BUDGET_USD", *(f"LLM_{p}_{k}" for p in
                    ("DISCOVERY", "EXTRACT", "SCORING", "TAILOR", "COVER") for k in ("MODEL", "EFFORT"))):
            monkeypatch.delenv(key, raising=False)
        for key, val in env.items():
            monkeypatch.setenv(key, val)
        db = tmp_path / "doctor.db"
        runner = CliRunner()
        with patch("strelitzia.config.DB_PATH", db), patch("strelitzia.database.DB_PATH", db), \
                patch("strelitzia.config.load_env", lambda: None):
            return runner.invoke(app, ["doctor"])

    def test_key_present_and_models_per_stage(self, tmp_path, monkeypatch) -> None:
        result = self._invoke(tmp_path, monkeypatch, {"ANTHROPIC_API_KEY": "sk-ant-secret-value",
                                                      "LLM_SCORING_EFFORT": "medium"})
        assert result.exit_code == 0
        assert "ANTHROPIC_API_KEY is set" in result.output
        assert "sk-ant-secret-value" not in result.output
        lines = {line.split()[0]: " ".join(line.split()[3:]) for line in result.output.splitlines()
                 if " model " in line and line.split()[0] in ("Discovery", "Extract", "Scoring", "Tailor", "Cover")}
        assert lines == {"Discovery": "claude-haiku-5-5 (effort low)", "Extract": "claude-haiku-5-5 (effort low)",
                         "Scoring": "claude-haiku-5-5 (effort medium)"}  # no Tailor or Cover: those run in Claude

    def test_missing_key_and_no_old_provider_lines(self, tmp_path, monkeypatch) -> None:
        result = self._invoke(tmp_path, monkeypatch, {"GEMINI_API_KEY": "g", "LLM_TAILOR_MODEL": "gemini-3.6-flash"})
        assert result.exit_code == 0
        assert "Set ANTHROPIC_API_KEY" in result.output
        assert "gemini" not in result.output.lower()

    def test_spend_against_budget_and_open_batches(self, tmp_path, monkeypatch) -> None:
        from strelitzia.database import close_connection, init_db
        from strelitzia.llm import record_usage

        db = tmp_path / "doctor.db"
        conn = init_db(db)
        record_usage("tailor", "claude-sonnet-5-5", {"input_tokens": 1_000_000}, conn=conn)  # $2.00
        conn.execute("INSERT INTO llm_batches (id, purpose, submitted_at, status, request_count, estimate_usd) "
                     "VALUES ('msgbatch_1', 'extract', '2026-10-13T01:00:00+00:00', 'submitted', 40, 0.5)")
        conn.commit()
        close_connection(db)
        result = self._invoke(tmp_path, monkeypatch, {"ANTHROPIC_API_KEY": "k", "LLM_MONTHLY_BUDGET_USD": "20"})
        assert result.exit_code == 0, result.output
        out = " ".join(result.output.split())  # rich wraps long lines
        assert "$2.00 + $0.50 estimated in open batches of $20.00 this month" in out
        assert "extract msgbatch_1 (40 requests" in out

    def test_no_database_yet(self, tmp_path, monkeypatch) -> None:
        result = self._invoke(tmp_path, monkeypatch, {"ANTHROPIC_API_KEY": "k"})
        assert result.exit_code == 0, result.output
        assert "$0.00 this month, no budget" in result.output
        assert "Open batches" in result.output

    def test_no_profile_files_checked(self, tmp_path, monkeypatch) -> None:
        result = self._invoke(tmp_path, monkeypatch, {"ANTHROPIC_API_KEY": "k"})
        assert result.exit_code == 0, result.output
        for name in ("profile.json", "resume.txt", "content_library.md", "searches.yaml", "Current tier"):
            assert name not in result.output

    def test_sign_in_off(self, tmp_path, monkeypatch) -> None:
        result = self._invoke(tmp_path, monkeypatch, {"ANTHROPIC_API_KEY": "k"})
        out = " ".join(result.output.split())
        assert "Google sign-in WARN Off: set STRELITZIA_GOOGLE_CLIENT_ID" in out
        assert "Drive connect WARN Needs Google sign-in" in out

    def test_sign_in_missing_keys(self, tmp_path, monkeypatch) -> None:
        result = self._invoke(tmp_path, monkeypatch, {"STRELITZIA_GOOGLE_CLIENT_ID": "cid",
                                                      "STRELITZIA_OWNER_EMAIL": "boss@example.com"})
        out = " ".join(result.output.split())
        assert "Google sign-in MISSING" in out
        assert "STRELITZIA_GOOGLE_CLIENT_SECRET, STRELITZIA_SESSION_SECRET is not" in out
        assert "cid" not in out.replace("CLIENT_ID", "")

    def test_sign_in_and_drive_ready(self, tmp_path, monkeypatch) -> None:
        from strelitzia.storage import drive

        monkeypatch.setattr(drive, "drive_libraries_installed", lambda: True)
        result = self._invoke(tmp_path, monkeypatch, {
            "STRELITZIA_GOOGLE_CLIENT_ID": "cid", "STRELITZIA_GOOGLE_CLIENT_SECRET": "client-secret-value",
            "STRELITZIA_SESSION_SECRET": "session-secret-value", "STRELITZIA_OWNER_EMAIL": "Boss@Example.com",
            "STRELITZIA_PUBLIC_URL": "https://jobs.example.com", "STRELITZIA_TOKEN_KEY": "token-key-value"})
        assert result.exit_code == 0, result.output
        out = " ".join(result.output.split())
        assert "Google sign-in OK On, owner boss@example.com" in out
        assert "Drive connect OK" in out and "Public URL" not in out
        for secret in ("client-secret-value", "session-secret-value", "token-key-value"):
            assert secret not in out
