#!/usr/bin/env python3
"""Launcher: shows model pickers then hands off to paper_processor."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from paper_processor import KNOWN_GOOD_CODE_MODELS, prompt_model_selection

chosen = prompt_model_selection()

print("  C++ / code section model:")
chosen_code = prompt_model_selection(KNOWN_GOOD_CODE_MODELS)

args = [
    sys.executable,
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "paper_processor.py"),
    "--model", chosen,
    "--code-model", chosen_code,
    *sys.argv[1:],
]

os.execv(sys.executable, args)
