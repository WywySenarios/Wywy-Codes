"""Shared fixtures for state tests."""

from __future__ import annotations

import pytest


@pytest.fixture
def state_file(tmp_path):
    return tmp_path / "state.json"
