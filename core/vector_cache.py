# Proprietary and confidential source code.
# Developer: Sunilkumar Pathipati
# Responsibility: Manages deterministic source-scoped vector-cache paths and persistence.
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile


@dataclass
class ScopedVectorCache:
    path: str

    @classmethod
    def for_source(cls, source_path: str | None, cache_dir: str, explicit_path: str | None = None):
        if explicit_path:
            return cls(os.path.abspath(os.path.expanduser(explicit_path)))
        absolute_source = os.path.abspath(os.path.expanduser(source_path or "source"))
        stem = re.sub(r"[^A-Za-z0-9_-]+", "_", Path(absolute_source).stem).strip("_") or "source"
        digest = hashlib.sha256(absolute_source.encode("utf-8")).hexdigest()[:8]
        return cls(os.path.join(cache_dir, f"vector_cache_{stem}_{digest}.json"))

    def exists(self) -> bool:
        return os.path.exists(self.path)

    def load(self) -> dict:
        if not self.exists():
            return {}
        try:
            with open(self.path, "r", encoding="utf-8") as handle:
                return json.load(handle)
        except (OSError, json.JSONDecodeError):
            return {}

    def save(self, value: dict) -> None:
        cache_dir = os.path.dirname(self.path) or "."
        os.makedirs(cache_dir, exist_ok=True)
        descriptor, temporary_path = tempfile.mkstemp(suffix=".json", dir=cache_dir)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(value, handle, indent=4)
            os.replace(temporary_path, self.path)
        except Exception:
            try:
                os.unlink(temporary_path)
            except OSError:
                pass
            raise

    def delete(self) -> bool:
        if not self.exists():
            return False
        os.remove(self.path)
        return True
