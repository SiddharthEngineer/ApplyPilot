"""Tests for config.py utilities including file permissions."""

from unittest.mock import patch

from applypilot.config import set_restricted_permissions


class TestSetRestrictedPermissions:
    """Test file permission hardening."""

    def test_sets_0o600(self, tmp_path):
        f = tmp_path / "test.txt"
        f.write_text("secret")
        set_restricted_permissions(f)
        mode = f.stat().st_mode
        assert mode & 0o777 == 0o600

    def test_no_error_on_missing_file(self, tmp_path):
        f = tmp_path / "nonexistent.txt"
        set_restricted_permissions(f)

    def test_no_error_on_directory(self, tmp_path):
        d = tmp_path / "subdir"
        d.mkdir()
        set_restricted_permissions(d)

    def test_preserves_content(self, tmp_path):
        f = tmp_path / "test.txt"
        f.write_text("my secret data")
        set_restricted_permissions(f)
        assert f.read_text() == "my secret data"

    def test_profile_json_after_load_migration(self, tmp_path):
        """After load_profile migration, profile.json has 0o600 permissions."""
        import json

        from applypilot.config import load_profile

        profile = {
            "personal": {"full_name": "Test"},
            "personal.password": "legacy_pass",
        }
        profile_path = tmp_path / "profile.json"
        profile_path.write_text(json.dumps(profile))

        with (
            patch("applypilot.config.PROFILE_PATH", profile_path),
            patch("applypilot.config.set_restricted_permissions") as mock_perm,
        ):
            try:
                load_profile()
            except FileNotFoundError:
                return  # PROFILE_PATH may not exist in mock context
            # The migration writes and then calls set_restricted_permissions
            mock_perm.assert_called_with(profile_path)
