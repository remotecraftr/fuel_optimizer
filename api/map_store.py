import threading
import time
from typing import Any, Dict, Optional

_STORE: Dict[str, Dict[str, Any]] = {}
_LOCK = threading.Lock()

# Optional TTL (seconds) for stored maps. Set to 24 hours by default.
DEFAULT_TTL = 24 * 3600


def save_map(map_id: str, geojson_obj: Dict[str, Any], ttl: int = DEFAULT_TTL) -> None:
    expire_at = time.time() + ttl if ttl else None
    with _LOCK:
        _STORE[map_id] = {"geojson": geojson_obj, "expire_at": expire_at}


def get_map(map_id: str) -> Optional[Dict[str, Any]]:
    with _LOCK:
        entry = _STORE.get(map_id)
        if not entry:
            return None
        expire_at = entry.get("expire_at")
        if expire_at and expire_at < time.time():
            # expired, remove
            del _STORE[map_id]
            return None
        return entry.get("geojson")


def _cleanup_loop(interval: int = 3600) -> None:
    while True:
        now = time.time()
        with _LOCK:
            expired = [k for k, v in _STORE.items() if v.get("expire_at") and v.get("expire_at") < now]
            for k in expired:
                del _STORE[k]
        time.sleep(interval)


# Start cleanup thread as daemon
_CLEANER = threading.Thread(target=_cleanup_loop, daemon=True)
_CLEANER.start()
