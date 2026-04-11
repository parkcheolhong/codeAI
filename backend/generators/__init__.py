from __future__ import annotations

from importlib import import_module
from typing import Any

__all__ = ["GenerationResult", "generate_python_project_bundle", "generate_non_python_project_bundle", "generate_multi_project_bundle"]


def __getattr__(name: str) -> Any:
    if name in __all__:
        facade = import_module("backend.generators.facade")
        return getattr(facade, name)
    raise AttributeError(f"module 'backend.generators' has no attribute {name!r}")
