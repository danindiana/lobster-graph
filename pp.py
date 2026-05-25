#!/usr/bin/env python3
"""Launcher: shows model picker then hands off to paper_processor."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from paper_processor import prompt_model_selection

chosen = prompt_model_selection()

args = [
    sys.executable,
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "paper_processor.py"),
    "--model", chosen,
    *sys.argv[1:],
]

os.execv(sys.executable, args)
