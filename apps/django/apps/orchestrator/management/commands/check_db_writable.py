"""Check that the SQLite database file is writable.

Exits with code 1 if the database file (or its parent directory)
is not writable by the current process.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Verify the SQLite database is writable by the current process."

    def handle(self, **_kwargs: object) -> None:
        db_path = settings.DATABASES["default"]["NAME"]
        path = Path(db_path)

        # Check parent directory is writable (needed for WAL/SHM files,
        # journal, and creating a new database)
        parent = path.parent
        if not os.access(parent, os.W_OK):
            self.stderr.write(
                f"Database directory is not writable: {parent}"
            )
            sys.exit(1)

        # Check file is writable if it exists
        if path.exists() and not os.access(path, os.W_OK):
            self.stderr.write(
                f"Database file is not writable: {path}"
            )
            sys.exit(1)
