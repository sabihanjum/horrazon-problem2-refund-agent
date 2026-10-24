"""
evidence_recorder.py - Immutable evidence log for all tool results.

Every tool call appends an entry to the recorder. The verifier reads
these entries to check:
  - SQL results are reproducible (hash matches)
  - Quoted document text exists verbatim
  - Claims cite valid evidence IDs
  - No claim relies silently on a failed tool
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any, Dict, List, Optional


class EvidenceEntry:
    def __init__(
        self,
        evidence_id: str,
        source_type: str,   # "sql" | "doc_search" | "doc_retrieval" | "whatsapp_sent" | "whatsapp_blocked"
        source_ref: str,    # SQL query or doc_id or search query
        data: Any,
        result_hash: str,
        timestamp: str,
        faulted: bool = False,
    ):
        self.evidence_id = evidence_id
        self.source_type = source_type
        self.source_ref = source_ref
        self.data = data
        self.result_hash = result_hash
        self.timestamp = timestamp
        self.faulted = faulted

    def to_dict(self):
        return {
            "evidence_id": self.evidence_id,
            "source_type": self.source_type,
            "source_ref": self.source_ref,
            "result_hash": self.result_hash,
            "timestamp": self.timestamp,
            "faulted": self.faulted,
        }


class EvidenceRecorder:
    """
    Append-only evidence store for a single agent run.

    Evidence IDs: EV-{seq:04d}
    """

    def __init__(self):
        self._entries: Dict[str, EvidenceEntry] = {}
        self._seq = 0

    def _next_id(self) -> str:
        self._seq += 1
        return f"EV-{self._seq:04d}"

    def _hash(self, data: Any) -> str:
        canonical = json.dumps(data, sort_keys=True, default=str)
        return hashlib.sha256(canonical.encode()).hexdigest()[:16]

    # ------------------------------------------------------------------
    # SQL
    # ------------------------------------------------------------------

    def record_sql(self, query: str, rows: List[dict], row_hash: str) -> str:
        eid = self._next_id()
        entry = EvidenceEntry(
            evidence_id=eid,
            source_type="sql",
            source_ref=query,
            data=rows,
            result_hash=row_hash,
            timestamp=datetime.utcnow().isoformat(),
        )
        self._entries[eid] = entry
        return eid

    # ------------------------------------------------------------------
    # Document search
    # ------------------------------------------------------------------

    def record_doc_search(self, query: str, results: List[dict]) -> str:
        eid = self._next_id()
        entry = EvidenceEntry(
            evidence_id=eid,
            source_type="doc_search",
            source_ref=query,
            data=results,
            result_hash=self._hash(results),
            timestamp=datetime.utcnow().isoformat(),
        )
        self._entries[eid] = entry
        return eid

    # ------------------------------------------------------------------
    # Document retrieval
    # ------------------------------------------------------------------

    def record_doc_retrieval(self, doc: Optional[dict], ref: str) -> str:
        eid = self._next_id()
        entry = EvidenceEntry(
            evidence_id=eid,
            source_type="doc_retrieval",
            source_ref=ref,
            data=doc,
            result_hash=self._hash(doc) if doc else "null",
            timestamp=datetime.utcnow().isoformat(),
        )
        self._entries[eid] = entry
        return eid

    # ------------------------------------------------------------------
    # WhatsApp
    # ------------------------------------------------------------------

    def record_whatsapp_sent(self, seller_id: str, message: str, agent: str) -> str:
        eid = self._next_id()
        payload = {"seller_id": seller_id, "message": message, "agent": agent}
        entry = EvidenceEntry(
            evidence_id=eid,
            source_type="whatsapp_sent",
            source_ref=seller_id,
            data=payload,
            result_hash=self._hash(payload),
            timestamp=datetime.utcnow().isoformat(),
        )
        self._entries[eid] = entry
        return eid

    def record_whatsapp_blocked(self, seller_id: str, message: str, reason: str) -> str:
        eid = self._next_id()
        payload = {"seller_id": seller_id, "message_preview": message[:100], "block_reason": reason}
        entry = EvidenceEntry(
            evidence_id=eid,
            source_type="whatsapp_blocked",
            source_ref=seller_id,
            data=payload,
            result_hash=self._hash(payload),
            timestamp=datetime.utcnow().isoformat(),
        )
        self._entries[eid] = entry
        return eid

    # ------------------------------------------------------------------
    # Lookup
    # ------------------------------------------------------------------

    def get(self, evidence_id: str) -> Optional[EvidenceEntry]:
        return self._entries.get(evidence_id)

    def all_entries(self) -> List[EvidenceEntry]:
        return list(self._entries.values())

    def failed_entries(self) -> List[EvidenceEntry]:
        return [e for e in self._entries.values() if e.faulted]

    def verify_sql_hash(self, evidence_id: str, claimed_hash: str) -> bool:
        """Check that the stored hash matches the claimed hash."""
        entry = self._entries.get(evidence_id)
        if entry is None or entry.source_type != "sql":
            return False
        return entry.result_hash == claimed_hash

    def find_doc_content(self, doc_id: str, quoted_text: str) -> bool:
        """Check that quoted_text appears verbatim in any retrieved version of doc_id."""
        for entry in self._entries.values():
            if entry.source_type not in ("doc_retrieval", "doc_search"):
                continue
            if entry.source_type == "doc_retrieval" and entry.source_ref == doc_id:
                doc = entry.data
                if doc and quoted_text in doc.get("content", ""):
                    return True
            if entry.source_type == "doc_search" and entry.data:
                for result in entry.data:
                    if result.get("doc_id") == doc_id:
                        snippet = result.get("snippet", "")
                        if quoted_text in snippet:
                            return True
        return False

    def to_dict(self) -> dict:
        return {eid: e.to_dict() for eid, e in self._entries.items()}
