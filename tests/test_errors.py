"""P9.3: client-facing error policy - friendly by classification.

Raw provider exceptions must never reach a kid's chat bubble: model names,
quota URLs and retry timers are internal detail (and mild info-leak).
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.api.chat import (friendly_error, FRIENDLY_GENERIC, FRIENDLY_QUOTA)


def test_quota_error_maps_to_friendly():
    exc = RuntimeError("429 RESOURCE_EXHAUSTED: quota exceeded, retry in 16h")
    assert friendly_error(exc) == FRIENDLY_QUOTA


def test_other_error_maps_to_generic():
    assert friendly_error(ValueError("boom")) == FRIENDLY_GENERIC


def test_friendly_messages_leak_nothing_internal():
    for msg in (FRIENDLY_QUOTA, FRIENDLY_GENERIC):
        low = msg.lower()
        assert "gemini" not in low and "groq" not in low
        assert "http" not in low and "429" not in low
        assert "model" not in low
