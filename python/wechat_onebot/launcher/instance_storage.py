"""Publish complete instance configurations with one filesystem transaction."""
from __future__ import annotations

import os
import shutil
import uuid
from pathlib import Path

from ..config import _atomic_write_json


def bind_runtime(config: dict, directory: Path, project: Path) -> None:
    helper = project / "vendor" / "helper"
    try:
        config["driver"]["helper_root"] = os.path.relpath(helper, directory)
    except ValueError:  # Different Windows drives cannot have a relative path.
        config["driver"]["helper_root"] = str(helper)


def publish_instance(directory: Path, row: dict, config: dict) -> None:
    # A normal mkdir inherits the workspace ACL, unlike TemporaryDirectory on
    # Windows. No incomplete manifest is exposed under instances/.
    staging = directory.parent.parent / ".staging"
    staging.mkdir(exist_ok=True)
    stage = staging / f"create-{uuid.uuid4().hex}"
    stage.mkdir()
    try:
        _atomic_write_json(stage / "config.json", config)
        _atomic_write_json(stage / "instance.json", row)
        stage.rename(directory)
    finally:
        if stage.exists():
            shutil.rmtree(stage)
