"""User-tunable settings persisted at the repo root.

Stored as ``./hushdoc_config.json`` (gitignored). Read on every config
endpoint hit — the file is tiny and always-fresh reads are simpler than
invalidating an in-memory cache when something writes from outside.

Writes go through a tempfile + ``os.replace`` so a crash mid-save can
never leave a half-written file the next process boot can't parse.
Defaults fill in for any missing key so the file can be hand-edited
without breaking the schema.

The launcher (``hushdoc.ps1``) also reads this file at exit time to
decide whether to skip the cleanup prompts -- both surfaces share one
source of truth.
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Any, Dict

logger = logging.getLogger("server.config")

# Repo-root location. Resolved relative to this file (../) so the path
# is stable regardless of the CWD uvicorn happened to launch from.
CONFIG_FILE = Path(__file__).resolve().parent.parent / "hushdoc_config.json"

# The only keys the GET/PUT endpoints accept. Anything else in the file
# is preserved on read (so future versions can add fields) but stripped
# on write.
DEFAULTS: Dict[str, Any] = {
    "model_path": "./models/model.gguf",
    "auto_cleanup_on_exit": False,
}


def read_config() -> Dict[str, Any]:
    """Return the persisted config, filling in defaults for any missing
    keys. Never raises -- a corrupt / unreadable file falls back to all
    defaults so the app still boots."""
    cfg = dict(DEFAULTS)
    try:
        if CONFIG_FILE.exists():
            data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                for k in DEFAULTS:
                    if k in data:
                        cfg[k] = data[k]
    except Exception:
        logger.exception("Failed to read %s; falling back to defaults.", CONFIG_FILE)
    return cfg


def write_config(updates: Dict[str, Any]) -> Dict[str, Any]:
    """Merge ``updates`` into the persisted config and return the result.
    Unknown keys are silently dropped. Writes are atomic (tempfile +
    ``os.replace``) so a Ctrl+C or power loss mid-save leaves the
    previous file intact."""
    cfg = read_config()
    for k, v in updates.items():
        if k in DEFAULTS:
            cfg[k] = v
    try:
        # NamedTemporaryFile with delete=False so we can rename it
        # after closing; otherwise on Windows the file is open by us
        # AND inaccessible to os.replace.
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=str(CONFIG_FILE.parent),
            delete=False,
            suffix=".tmp",
        ) as tmp:
            json.dump(cfg, tmp, indent=2)
            tmp_path = Path(tmp.name)
        os.replace(tmp_path, CONFIG_FILE)
        logger.info("Wrote %s: %s", CONFIG_FILE.name, list(updates.keys()))
    except Exception:
        logger.exception("Failed to write %s", CONFIG_FILE)
    return cfg
