"""Media rebuild policy: debounce, coalesce and rate-limit archive jobs.

The policy is pure logic (no I/O) so it can be unit tested without the daemon:

* a change must persist for `settle` seconds before it is committed;
* continuous changes force a commit after `max_wait` seconds;
* the caller runs at most one job at a time and one queued follow-up.
"""
MEDIA_SCAN_SECONDS = 20
MEDIA_SETTLE_SECONDS = 20
MEDIA_MAX_WAIT_SECONDS = 180
MEDIA_MIN_INTERVAL_SECONDS = 45
MEDIA_REFRESH_SECONDS = 3600


class MediaTrigger:
    """Debounce media source signatures so a burst of writes rebuilds the archive once.

    A change only counts after the source has been quiet for `settle` seconds. If it keeps
    changing, a rebuild is forced after `max_wait` seconds. Returning the signature means
    "commit this source state and queue one rebuild"; returning None means "keep waiting".
    """

    def __init__(self, settle=MEDIA_SETTLE_SECONDS, max_wait=MEDIA_MAX_WAIT_SECONDS):
        self.settle, self.max_wait = settle, max_wait
        self.pending = None
        self.changed_at = 0.0
        self.burst_at = 0.0

    def observe(self, committed, sig, now):
        if committed == sig:
            self.pending = None
            return None
        if self.pending is None:
            self.pending, self.changed_at, self.burst_at = sig, now, now
            return None
        if sig != self.pending:
            self.pending, self.changed_at = sig, now
        if now - self.changed_at >= self.settle or now - self.burst_at >= self.max_wait:
            self.pending = None
            return sig
        return None
