# app/core/supabase_safe.py
from __future__ import annotations

import time
from typing import Any


TRANSIENT_SUPABASE_EXCEPTIONS = (
    KeyError,
)


def execute_with_retry(builder: Any, retries: int = 2, base_delay: float = 0.2):
    last_exc = None

    for attempt in range(retries + 1):
        try:
            return builder.execute()
        except TRANSIENT_SUPABASE_EXCEPTIONS as exc:
            last_exc = exc
            if attempt >= retries:
                raise
            time.sleep(base_delay * (attempt + 1))

    if last_exc:
        raise last_exc