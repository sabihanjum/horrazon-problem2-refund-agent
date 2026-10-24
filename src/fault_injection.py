"""
fault_injection.py - Deterministic fault injector for tool calls.

Uses a fixed seed to produce a reproducible sequence of faults.
Approximately 15% of tool calls are disrupted with one of:
  - TIMEOUT  (simulated timeout)
  - EMPTY    (empty result returned)
  - FAIL     (hard failure / exception simulation)

Fault types are weighted 5:5:5 (equal).
"""

from __future__ import annotations

import random
from typing import Optional


FAULT_SEED = 42
FAULT_RATE = 0.15

FAULT_TYPES = ["TIMEOUT", "EMPTY", "FAIL"]
FAULT_MESSAGES = {
    "TIMEOUT": "Tool call timed out after 5000ms. Partial results unavailable.",
    "EMPTY":   "Tool returned empty results (no data found for this query).",
    "FAIL":    "Tool call failed: internal server error (simulated).",
}


class FaultInjector:
    """
    Deterministic fault injector.

    Usage:
        injector = FaultInjector(seed=42, rate=0.15)
        error = injector.maybe_fail("run_sql")
        if error:
            return ToolResult(success=False, error=error)
    """

    def __init__(self, seed: int = FAULT_SEED, rate: float = FAULT_RATE):
        self._rng = random.Random(seed)
        self.rate = rate
        self.call_log: list = []

    def maybe_fail(self, tool_name: str) -> Optional[str]:
        """
        Return an error string if this call should fail, else None.
        Logs every call for reproducibility.
        """
        roll = self._rng.random()
        should_fail = roll < self.rate
        fault_type = self._rng.choice(FAULT_TYPES) if should_fail else None
        self.call_log.append({
            "tool": tool_name,
            "roll": round(roll, 4),
            "faulted": should_fail,
            "fault_type": fault_type,
        })
        if should_fail:
            return f"[{fault_type}] {FAULT_MESSAGES[fault_type]}"
        return None

    def reset(self):
        """Reset the RNG to the initial seed (for reproducible test runs)."""
        self._rng = random.Random(FAULT_SEED)
        self.call_log.clear()

    def get_fault_summary(self) -> dict:
        total = len(self.call_log)
        faulted = sum(1 for c in self.call_log if c["faulted"])
        return {
            "total_calls": total,
            "faulted_calls": faulted,
            "fault_rate": faulted / max(total, 1),
            "by_tool": {
                t: sum(1 for c in self.call_log if c["tool"] == t and c["faulted"])
                for t in set(c["tool"] for c in self.call_log)
            },
        }
