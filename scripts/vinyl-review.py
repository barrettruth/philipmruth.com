#!/usr/bin/env python3

from pathlib import Path
import runpy
import sys

sys.argv = [sys.argv[0], "review", *sys.argv[1:]]
runpy.run_path(str(Path(__file__).with_name("vinyl_pipeline.py")), run_name="__main__")
