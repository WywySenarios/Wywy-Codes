"""Tests for management commands."""

from __future__ import annotations

import pytest
from django.conf import settings
from django.core.management import call_command


class TestCheckDbWritable:
    """Verify the check_db_writable management command detects
    whether the SQLite database file and its parent directory
    are writable by the current process."""

    def test_succeeds_when_db_is_writable(self, db):
        """The command should exit cleanly when the database is writable."""
        call_command("check_db_writable")

    def test_fails_when_db_not_writable(self, tmp_path, monkeypatch):
        """The command should raise SystemExit(1) when the database
        is not writable by the current process."""
        unwritable = tmp_path / "db.sqlite3"
        unwritable.write_text("")
        unwritable.chmod(0o444)
        tmp_path.chmod(0o555)

        monkeypatch.setattr(settings, "DATABASES", {
            "default": {
                "ENGINE": "django.db.backends.sqlite3",
                "NAME": str(unwritable),
            }
        })

        with pytest.raises(SystemExit) as exc_info:
            call_command("check_db_writable")
        assert exc_info.value.code == 1
