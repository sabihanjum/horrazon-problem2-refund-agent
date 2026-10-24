"""
test_injection_gate.py - Tests for the WhatsApp injection gate.

Verifies that:
  1. Blatant English injection (INJ-001) is blocked.
  2. Subtle Hinglish injection (INJ-002) is blocked.
  3. Legitimate authorized requests pass.
  4. Requests from non-authorized agents are blocked.
  5. Invalid seller IDs are blocked.
  6. Messages from documents only (user_originated=False) are blocked.
  7. Messages containing injection phrases are blocked regardless of source.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.tools import WhatsAppGate, _wa_gate
from src.evidence_recorder import EvidenceRecorder
from src.fault_injection import FaultInjector
from src.tools import send_seller_whatsapp, DocumentStore


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

LEGIT_MESSAGE = (
    "Dear Seller, refund REF00045 for Order ORD00123 has been escalated. "
    "Please respond within 2 business days to avoid auto-approval and penalty. "
    "Regards, Horrazon Refund War Room"
)


def make_gate() -> WhatsAppGate:
    return WhatsAppGate()


# ---------------------------------------------------------------------------
# Test 1: Blatant English injection (INJ-001 as source) is blocked
# ---------------------------------------------------------------------------

def test_injection_001_blocked_by_source_doc():
    gate = make_gate()
    allowed, reason = gate.check(
        seller_id="SEL001",
        message=LEGIT_MESSAGE,
        authorized_by="Priya R",
        user_originated=True,
        source_doc_ids=["INJ-001"],  # request came from injection doc
    )
    assert not allowed, f"Expected blocked, got allowed. Reason: {reason}"
    assert "INJ-001" in reason


# ---------------------------------------------------------------------------
# Test 2: Hinglish injection (INJ-002 as source) is blocked
# ---------------------------------------------------------------------------

def test_injection_002_blocked_by_source_doc():
    gate = make_gate()
    allowed, reason = gate.check(
        seller_id="SEL003",
        message=LEGIT_MESSAGE,
        authorized_by="Priya R",
        user_originated=True,
        source_doc_ids=["INJ-002"],
    )
    assert not allowed, f"Expected blocked, got allowed. Reason: {reason}"
    assert "INJ-002" in reason


# ---------------------------------------------------------------------------
# Test 3: Injection phrase in message content is blocked (regardless of source)
# ---------------------------------------------------------------------------

def test_override_phrase_in_message_blocked():
    gate = make_gate()
    inject_msg = (
        "Dear SEL001, this is a system override instruction. "
        "Your account has been permanently suspended immediately. "
        "Regards, Horrazon"
    )
    allowed, reason = gate.check(
        seller_id="SEL001",
        message=inject_msg,
        authorized_by="Priya R",
        user_originated=True,
        source_doc_ids=[],
    )
    assert not allowed
    assert "injection indicator" in reason.lower()


# ---------------------------------------------------------------------------
# Test 4: Hinglish injection phrase in message content is blocked
# ---------------------------------------------------------------------------

def test_hinglish_phrase_in_message_blocked():
    gate = make_gate()
    hinglish_msg = (
        "Namaste SEL003, abhi turant aapka account deactivate kiya ja raha hai. "
        "Koi response mat dena. Regards, Team"
    )
    allowed, reason = gate.check(
        seller_id="SEL003",
        message=hinglish_msg,
        authorized_by="Karthik S",
        user_originated=True,
        source_doc_ids=[],
    )
    assert not allowed
    assert "injection indicator" in reason.lower()


# ---------------------------------------------------------------------------
# Test 5: user_originated=False is blocked
# ---------------------------------------------------------------------------

def test_not_user_originated_blocked():
    gate = make_gate()
    allowed, reason = gate.check(
        seller_id="SEL001",
        message=LEGIT_MESSAGE,
        authorized_by="Priya R",
        user_originated=False,  # automated pipeline only
    )
    assert not allowed
    assert "user_originated" in reason


# ---------------------------------------------------------------------------
# Test 6: Unknown authorized_by agent is blocked
# ---------------------------------------------------------------------------

def test_unknown_agent_blocked():
    gate = make_gate()
    allowed, reason = gate.check(
        seller_id="SEL001",
        message=LEGIT_MESSAGE,
        authorized_by="Hacker McHackface",
        user_originated=True,
    )
    assert not allowed
    assert "not an authorized agent" in reason


# ---------------------------------------------------------------------------
# Test 7: Invalid seller ID format is blocked
# ---------------------------------------------------------------------------

def test_invalid_seller_id_blocked():
    gate = make_gate()
    allowed, reason = gate.check(
        seller_id="SELLER-ABC",  # wrong format
        message=LEGIT_MESSAGE,
        authorized_by="Priya R",
        user_originated=True,
    )
    assert not allowed
    assert "seller_id" in reason.lower()


# ---------------------------------------------------------------------------
# Test 8: Message too short is blocked
# ---------------------------------------------------------------------------

def test_message_too_short_blocked():
    gate = make_gate()
    allowed, reason = gate.check(
        seller_id="SEL001",
        message="Hi",
        authorized_by="Priya R",
        user_originated=True,
    )
    assert not allowed
    assert "short" in reason.lower()


# ---------------------------------------------------------------------------
# Test 9: Legitimate request passes all gates
# ---------------------------------------------------------------------------

def test_legitimate_request_passes():
    gate = make_gate()
    allowed, reason = gate.check(
        seller_id="SEL001",
        message=LEGIT_MESSAGE,
        authorized_by="Priya R",
        user_originated=True,
        source_doc_ids=["PROC-001"],  # legitimate source
    )
    assert allowed, f"Expected allowed, got blocked. Reason: {reason}"
    assert reason == "OK"


# ---------------------------------------------------------------------------
# Test 10: send_seller_whatsapp tool integration - injection blocked end-to-end
# ---------------------------------------------------------------------------

def test_send_whatsapp_tool_blocks_injection_source():
    recorder = EvidenceRecorder()
    injector = FaultInjector(seed=99)  # different seed, won't inject fault on first call
    injector._rng.random()  # advance rng past first fault check

    # Use a no-fault injector to test gate logic cleanly
    class NoFaultInjector:
        def maybe_fail(self, tool_name):
            return None

    result = send_seller_whatsapp(
        seller_id="SEL001",
        message=LEGIT_MESSAGE,
        authorized_by="Karthik S",
        user_originated=True,
        recorder=recorder,
        injector=NoFaultInjector(),
        source_doc_ids=["INJ-001"],  # injection source
        log_path="data/test_wa_log.jsonl",
    )

    assert not result.success, "Expected blocked result"
    assert "INJ-001" in result.error


# ---------------------------------------------------------------------------
# Test 11: send_seller_whatsapp tool integration - legitimate passes
# ---------------------------------------------------------------------------

def test_send_whatsapp_tool_passes_legit():
    recorder = EvidenceRecorder()

    class NoFaultInjector:
        def maybe_fail(self, tool_name):
            return None

    result = send_seller_whatsapp(
        seller_id="SEL005",
        message=LEGIT_MESSAGE,
        authorized_by="Deepa M",
        user_originated=True,
        recorder=recorder,
        injector=NoFaultInjector(),
        source_doc_ids=["PROC-001"],
        log_path="data/test_wa_log.jsonl",
    )

    assert result.success, f"Expected success, got: {result.error}"
    assert result.data["status"] == "logged"


# ---------------------------------------------------------------------------
# Test 12: Fault injection blocks tool call
# ---------------------------------------------------------------------------

def test_fault_injection_blocks_whatsapp():
    recorder = EvidenceRecorder()

    class AlwaysFaultInjector:
        def maybe_fail(self, tool_name):
            return "[FAIL] Simulated failure for testing."

    result = send_seller_whatsapp(
        seller_id="SEL001",
        message=LEGIT_MESSAGE,
        authorized_by="Priya R",
        user_originated=True,
        recorder=recorder,
        injector=AlwaysFaultInjector(),
        source_doc_ids=[],
        log_path="data/test_wa_log.jsonl",
    )
    assert not result.success
    assert "FAIL" in result.error
