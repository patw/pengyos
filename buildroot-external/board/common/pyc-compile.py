#!/usr/bin/env python3
"""Byte-compile Python files for the target, one fresh interpreter per call.

    cd $(TARGET_DIR) && pyc-compile.py [--legacy] usr/lib/python3.X/a.py ...

Replaces Buildroot's single `compileall` run (external.mk explains why):
marshal shares objects by reference count, so a .pyc's bytes depend on what
the compiling process did before it. Called with one file at a time, the bytes
depend on that file only. Same results as `compileall -b -s TARGET -p /`:
--legacy writes foo.pyc beside foo.py (Buildroot's PYC_ONLY); the recorded
source path is the on-target one. The invalidation mode follows
SOURCE_DATE_EPOCH (checked hash), as compileall's does.
"""
import importlib.util
import py_compile
import sys

args = sys.argv[1:]
legacy = bool(args) and args[0] == "--legacy"
for path in args[1:] if legacy else args:
    cfile = path + "c" if legacy else importlib.util.cache_from_source(path)
    py_compile.compile(path, cfile=cfile, dfile="/" + path, doraise=True)
