"""Vercel's FastAPI entrypoint for the backend package in this repository."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "backend"))

from app.main import configured_app  # noqa: E402

app = configured_app()
