#!/usr/bin/env python3
"""Codespaces/root launcher for the nested EMA-BAND backtest."""

from pathlib import Path
import runpy
import sys

PROJECT_DIR = Path(__file__).resolve().parent / "EMA-BAND"
TARGET = PROJECT_DIR / "backtest.py"

if not TARGET.is_file():
    raise SystemExit(f"Backtest entrypoint not found: {TARGET}")

sys.path.insert(0, str(PROJECT_DIR))
runpy.run_path(str(TARGET), run_name="__main__")
