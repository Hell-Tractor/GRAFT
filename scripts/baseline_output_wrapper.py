#!/usr/bin/env python3
"""Output-location adapter only: keep RPG-generated files outside readonly sources.

Cargo runs rustdoc in the package directory. RPG writes relative to cwd; resolve the
input source before changing cwd, then forward the exact remaining rustdoc arguments.
"""
import os
import sys

args=[os.path.abspath(x) if x.endswith('.rs') and os.path.isfile(x) else x for x in sys.argv[1:]]
os.chdir(os.environ['GRAFT_BASELINE_OUTPUT_DIR'])
real=os.environ['GRAFT_REAL_RUSTDOC']
os.execv(real,[real]+args)
