"""This file is intentionally left empty.

The old api_log_tail endpoint was removed and replaced by:
  - GET /api/pipelines/<id>/logs/              → list log files
  - GET /api/pipelines/<id>/logs/entries/<f>   → per-file JSON entries
  - GET /api/pipelines/<id>/logs/entries/<f>/?raw → raw text dump

See test_views_log_files.py for coverage of the new endpoints.
"""
