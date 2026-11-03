"""Smoke test: import every module under app/ so import errors surface."""

import importlib
import pkgutil

import app


def test_import_all_app_modules():
    names = [m.name for m in pkgutil.walk_packages(app.__path__, prefix="app.")]
    for name in names:
        importlib.import_module(name)
    assert names
