from __future__ import annotations

import importlib.util
from pathlib import Path

from .api import Solution


def load_solution(path: str | Path) -> Solution:
    path = Path(path)
    module_name = f"_shzio_solution_{path.stem}"
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load solution module {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    candidates = [
        value
        for value in vars(module).values()
        if isinstance(value, type) and issubclass(value, Solution) and value is not Solution
    ]
    if not candidates:
        raise ValueError(f"{path} does not define a Solution subclass")
    if len(candidates) > 1:
        names = ", ".join(cls.__name__ for cls in candidates)
        raise ValueError(f"{path} defines multiple Solution subclasses: {names}")
    return candidates[0]()

