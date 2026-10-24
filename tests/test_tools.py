"""
test_tools.py - Unit tests for read tools, SQL safety, verifier, and conflict resolution.
"""

from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.tools import run_sql, _is_read_only, _hash_rows
from src.evidence_recorder import EvidenceRecorder
from src.fault_injection import FaultInjector
from src.verifier import Verifier, Claim, resolve_conflict


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def test_db():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE refunds (refund_id TEXT, amount REAL, status TEXT)")
    conn.execute("INSERT INTO refunds VALUES ('REF001', 1500.0, 'approved')")
    conn.execute("INSERT INTO refunds VALUES ('REF002', 500.0, 'pending')")
    conn.execute("INSERT INTO refunds VALUES ('REF003', 2000.0, 'rejected')")
    conn.commit()
    conn.close()
    yield db_path
    os.unlink(db_path)


class NoFaultInjector:
    def maybe_fail(self, _):
        return None


# ---------------------------------------------------------------------------
# SQL safety tests
# ---------------------------------------------------------------------------

def test_read_only_allows_select():
    assert _is_read_only("SELECT * FROM refunds WHERE status = 'pending'")


def test_read_only_blocks_insert():
    assert not _is_read_only("INSERT INTO refunds VALUES ('X', 0, 'test')")


def test_read_only_blocks_update():
    assert not _is_read_only("UPDATE refunds SET status = 'approved' WHERE refund_id = 'REF001'")


def test_read_only_blocks_delete():
    assert not _is_read_only("DELETE FROM refunds WHERE status = 'pending'")


def test_read_only_blocks_drop():
    assert not _is_read_only("DROP TABLE refunds")


def test_read_only_blocks_pragma_write():
    assert not _is_read_only("PRAGMA journal_mode = WAL")


def test_run_sql_success(test_db):
    recorder = EvidenceRecorder()
    result = run_sql(
        "SELECT * FROM refunds WHERE status = 'approved'",
        test_db, recorder, NoFaultInjector()
    )
    assert result.success
    assert len(result.data["rows"]) == 1
    assert result.data["rows"][0]["refund_id"] == "REF001"
    assert result.evidence_id.startswith("EV-")


def test_run_sql_blocked_write(test_db):
    recorder = EvidenceRecorder()
    result = run_sql(
        "DELETE FROM refunds",
        test_db, recorder, NoFaultInjector()
    )
    assert not result.success
    assert "write" in result.error.lower()


def test_run_sql_hash_reproducible(test_db):
    recorder1 = EvidenceRecorder()
    recorder2 = EvidenceRecorder()
    q = "SELECT * FROM refunds ORDER BY refund_id"
    r1 = run_sql(q, test_db, recorder1, NoFaultInjector())
    r2 = run_sql(q, test_db, recorder2, NoFaultInjector())
    assert r1.data["hash"] == r2.data["hash"]


# ---------------------------------------------------------------------------
# Evidence recorder tests
# ---------------------------------------------------------------------------

def test_recorder_sql_verify_hash(test_db):
    recorder = EvidenceRecorder()
    result = run_sql("SELECT * FROM refunds", test_db, recorder, NoFaultInjector())
    eid = result.evidence_id
    stored_hash = result.data["hash"]
    assert recorder.verify_sql_hash(eid, stored_hash)
    assert not recorder.verify_sql_hash(eid, "wronghash")


# ---------------------------------------------------------------------------
# Verifier tests
# ---------------------------------------------------------------------------

def test_verifier_passes_valid_sql_claim(test_db):
    recorder = EvidenceRecorder()
    result = run_sql("SELECT COUNT(*) as n FROM refunds", test_db, recorder, NoFaultInjector())
    claim = Claim(
        claim_id="CLM-001",
        claim_type="fact",
        text="There are 3 refunds.",
        evidence_id=result.evidence_id,
        numerical_value=3.0,
    )
    verifier = Verifier(recorder)
    report = verifier.verify([claim])
    assert "CLM-001" in report.verified_claims, report.failed_claims


def test_verifier_flags_superseded_doc():
    recorder = EvidenceRecorder()
    # Record a fake doc retrieval of POL-002 (superseded)
    fake_doc = {"doc_id": "POL-002", "content": "Old refund cap is Rs. 5,000.", "effective_date": "2023-01-01"}
    eid = recorder.record_doc_retrieval(fake_doc, "POL-002")

    claim = Claim(
        claim_id="CLM-002",
        claim_type="fact",
        text="Max refund is Rs. 5,000 (from POL-002).",
        evidence_id=eid,
        doc_id="POL-002",
    )
    verifier = Verifier(recorder)
    report = verifier.verify([claim])
    assert len(report.superseded_flags) > 0
    assert any("POL-002" in f for f in report.superseded_flags)


def test_verifier_flags_injection_doc():
    recorder = EvidenceRecorder()
    fake_doc = {"doc_id": "INJ-001", "content": "Override mode...", "effective_date": "2024-01-01"}
    eid = recorder.record_doc_retrieval(fake_doc, "INJ-001")

    claim = Claim(
        claim_id="CLM-INJ",
        claim_type="fact",
        text="Suspend seller SEL001 as instructed.",
        evidence_id=eid,
        doc_id="INJ-001",
    )
    verifier = Verifier(recorder)
    report = verifier.verify([claim])
    assert not report.overall_pass
    assert len(report.injection_flags) > 0


# ---------------------------------------------------------------------------
# Conflict resolution tests
# ---------------------------------------------------------------------------

def test_conflict_resolution_later_date_wins():
    doc_a = {"doc_id": "POL-001", "title": "Policy v2", "effective_date": "2024-01-01", "authority": "Head of Customer Experience"}
    doc_b = {"doc_id": "POL-002", "title": "Policy v1", "effective_date": "2023-01-01", "authority": "Head of Operations"}
    result = resolve_conflict(doc_a, doc_b, "refund window")
    assert result.winner == "POL-001"
    assert "effective date" in result.resolution.lower()


def test_conflict_resolution_higher_authority_wins():
    doc_a = {"doc_id": "DOC-A", "title": "Policy", "effective_date": "2024-01-01", "authority": "Head of Customer Experience"}
    doc_b = {"doc_id": "DOC-B", "title": "Policy", "effective_date": "2024-01-01", "authority": "Operations Team"}
    result = resolve_conflict(doc_a, doc_b, "refund timeline")
    assert result.winner == "DOC-A"
    assert "authority" in result.resolution.lower()


def test_conflict_resolution_unresolved_when_tied():
    doc_a = {"doc_id": "DOC-X", "title": "Policy", "effective_date": "2024-01-01", "authority": "Operations"}
    doc_b = {"doc_id": "DOC-Y", "title": "Policy", "effective_date": "2024-01-01", "authority": "Operations"}
    result = resolve_conflict(doc_a, doc_b, "some topic")
    assert result.winner == "UNRESOLVED"


# ---------------------------------------------------------------------------
# Fault injection tests
# ---------------------------------------------------------------------------

def test_fault_injector_deterministic():
    injector = FaultInjector(seed=42, rate=0.15)
    faults = [injector.maybe_fail("run_sql") for _ in range(100)]

    injector2 = FaultInjector(seed=42, rate=0.15)
    faults2 = [injector2.maybe_fail("run_sql") for _ in range(100)]

    assert faults == faults2, "Fault injector must be deterministic with same seed"


def test_fault_injector_rate_approx():
    injector = FaultInjector(seed=42, rate=0.15)
    faults = [injector.maybe_fail("run_sql") for _ in range(1000)]
    fault_count = sum(1 for f in faults if f is not None)
    # Should be approximately 15% ± 3%
    assert 100 < fault_count < 200, f"Fault rate out of expected range: {fault_count}/1000"
