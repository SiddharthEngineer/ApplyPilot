"""Tests for config.py utilities including file permissions."""

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

