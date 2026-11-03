"""Keep temporarily unreadable source links across container updates and restarts."""
import json
import threading
from dataclasses import replace
from src.app_paths import data_dir
from src.sources.common import normalize_activity_id, opus_link

_lock = threading.RLock()


def _load():
    path = data_dir() / 'source_content_retry.json'
    return json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}


def include_pending(result):
    with _lock:
        pending = _load().get(result.source_id, [])
    if not pending:
        return result
    combined = list(dict.fromkeys(
        result.activity_links + [opus_link(did) for did in pending]
    ))
    merged = replace(result, updated=True)
    # The normal source cap must not discard older pending links.
    merged.activity_links = combined
    return merged


def save_pending(results, deferred_ids):
    deferred = set(deferred_ids)
    with _lock:
        state = _load()
        for result in results:
            if result.updated:
                ids = [normalize_activity_id(link) for link in result.activity_links]
                state[result.source_id] = list(dict.fromkeys(did for did in ids if did in deferred))
        path = data_dir() / 'source_content_retry.json'
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix('.tmp')
        temporary.write_text(json.dumps(state, ensure_ascii=False), encoding='utf-8')
        temporary.replace(path)
