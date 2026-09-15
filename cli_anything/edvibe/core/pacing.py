"""Human-like pacing between Edvibe RPC calls — a product safety rail.

The CLI is documented and shared with teachers; every call is jittered and
rate-capped so the traffic looks like one teacher working manually:

  * 0.8-2.0 s random pause between calls (jitter, never a fixed rhythm)
  * at most 150 calls per rolling hour per process (hard stop)

Tunables (env): EDVIBE_PACE_MIN / EDVIBE_PACE_MAX / EDVIBE_PACE_CAP,
disable entirely for local tests with EDVIBE_PACE_OFF=1.
"""
import os
import random
import time

MIN_INTERVAL = 0.8
MAX_INTERVAL = 2.0
HOURLY_CAP = 150


class PaceLimitError(RuntimeError):
    """Raised when the rolling hourly call cap is reached."""


class Pacer:
    def __init__(self, min_interval=None, max_interval=None, hourly_cap=None,
                 enabled=None):
        self.enabled = (os.environ.get("EDVIBE_PACE_OFF") != "1") \
            if enabled is None else enabled
        self.min_interval = float(
            os.environ.get("EDVIBE_PACE_MIN", min_interval or MIN_INTERVAL))
        self.max_interval = float(
            os.environ.get("EDVIBE_PACE_MAX", max_interval or MAX_INTERVAL))
        self.hourly_cap = int(
            os.environ.get("EDVIBE_PACE_CAP", hourly_cap or HOURLY_CAP))
        self._last = 0.0
        self._stamps = []
        self.total = 0

    def wait(self):
        """Sleep a jittered interval; raise after `hourly_cap` calls/hour."""
        if not self.enabled:
            self.total += 1
            return
        now = time.time()
        self._stamps = [t for t in self._stamps if now - t < 3600]
        if len(self._stamps) >= self.hourly_cap:
            raise PaceLimitError(
                f"Hourly call cap ({self.hourly_cap} calls/hour) reached - "
                f"this is deliberate, so the traffic looks like one teacher "
                f"working manually. Continue later (or raise EDVIBE_PACE_CAP "
                f"for tests).")
        delay = random.uniform(self.min_interval, self.max_interval)
        elapsed = now - self._last
        if elapsed < delay:
            time.sleep(delay - elapsed)
        self._last = time.time()
        self._stamps.append(self._last)
        self.total += 1
