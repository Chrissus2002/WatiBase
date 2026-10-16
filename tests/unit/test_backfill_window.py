import sys
from datetime import datetime, timezone
from pathlib import Path

# Allow importing shared modules without installing them
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "shared"))

from batch_message_import import in_window


def test_window_reads_every_wati_timestamp_shape_as_utc():
    since = datetime(2026, 10, 9, 16, 0, tzinfo=timezone.utc)
    until = datetime(2026, 10, 12, 1, 0, tzinfo=timezone.utc)

    # getMessages returns `created` both with and without a `Z` suffix.
    assert in_window({"created": "2026-10-10T12:00:00.123"}, since, until)
    assert in_window({"created": "2026-10-10T12:00:00.123Z"}, since, until)
    # Webhook-shaped payloads carry epoch seconds in `timestamp`.
    assert in_window({"timestamp": "1791633600"}, since, until)

    # The window is half-open, and undated items are never staged.
    assert in_window({"created": "2026-10-09T16:00:00Z"}, since, until)
    assert not in_window({"created": "2026-10-12T01:00:00Z"}, since, until)
    assert not in_window({"created": "2026-10-09T15:59:59"}, since, until)
    assert not in_window({}, since, until)
