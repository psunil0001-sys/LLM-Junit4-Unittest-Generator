# Proprietary and confidential source code.
# Developer: Sunilkumar Pathipati
# Responsibility: Builds generic file-vector indexes through injected language callbacks.
import hashlib
import os
from typing import Callable

from UnitTest_gen.core.vector_cache import ScopedVectorCache


def file_md5(path: str) -> str:
    digest = hashlib.md5()
    with open(path, "rb") as handle:
        digest.update(handle.read())
    return digest.hexdigest()


def build_vector_index(
    scan_root: str,
    cache: ScopedVectorCache,
    *,
    file_filter: Callable[[str, str], bool],
    item_key_fn: Callable[[str, str], str],
    vector_fn: Callable[[str], object],
    vectors_enabled_fn: Callable[[], bool],
    write_cache: bool = True,
) -> dict:
    index = {}
    cached_values = cache.load()
    cache_updated = False
    if not os.path.exists(scan_root):
        return index

    for root, directories, files in os.walk(scan_root):
        directories[:] = [name for name in directories if name not in {"build", ".gradle", ".git"}]
        for file_name in files:
            path = os.path.join(root, file_name)
            if not file_filter(path, scan_root):
                continue
            with open(path, "r", encoding="utf-8") as handle:
                content = handle.read()
            item_key = item_key_fn(path, content)
            absolute_path = os.path.abspath(path)
            checksum = file_md5(path)
            cached = cached_values.get(absolute_path, cached_values.get(item_key, {}))
            current = cached.get("md5") == checksum
            has_vector = cached.get("vector") is not None
            if current and (has_vector or not vectors_enabled_fn()):
                vector = cached.get("vector")
            else:
                vector = vector_fn(content)
                if vector is not None or not vectors_enabled_fn():
                    cached_values[absolute_path] = {
                        "class_name": item_key,
                        "path": absolute_path,
                        "md5": checksum,
                        "vector": vector,
                    }
                    cache_updated = True
            index_key = item_key
            if index_key in index and os.path.abspath(index[index_key]["path"]) != absolute_path:
                index_key = f"{item_key}@{os.path.relpath(path, scan_root)}"
            index[index_key] = {"class_name": item_key, "path": path, "content": content, "vector": vector}
    if cache_updated and write_cache:
        cache.save(cached_values)
    return index
