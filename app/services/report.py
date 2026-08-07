from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from pydantic import BaseModel


def atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def atomic_write_model(path: Path, model: BaseModel) -> None:
    atomic_write_text(path, json.dumps(model.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n")
