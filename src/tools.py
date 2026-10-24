"""
tools.py - Read and side-effect tools for the Refund War Room agent.

Tools:
  run_sql(query)                   - read-only SQL against SQLite
  search_docs(query)               - TF-IDF keyword search over documents
  get_policy(name, as_of_date)     - date-aware policy retrieval
  get_document(doc_id)             - direct document retrieval by ID
  send_seller_whatsapp(...)        - side-effect tool with multi-gate protection

All tool calls are wrapped by fault_injection.maybe_fail() which uses
a fixed seed to deterministically inject 15% failures.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import sqlite3
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from src.fault_injection import FaultInjector
from src.evidence_recorder import EvidenceRecorder


# ---------------------------------------------------------------------------
# Tool result schema
# ---------------------------------------------------------------------------

class ToolResult:
    """Structured result from any tool call."""

    def __init__(
        self,
        tool: str,
        success: bool,
        data: Any = None,
        error: str = "",
        evidence_id: str = "",
    ):
        self.tool = tool
        self.success = success
        self.data = data
        self.error = error
        self.evidence_id = evidence_id

    def to_dict(self):
        return {
            "tool": self.tool,
            "success": self.success,
            "data": self.data,
            "error": self.error,
            "evidence_id": self.evidence_id,
        }

    def __repr__(self):
        if self.success:
            return f"ToolResult({self.tool}, ok, evidence={self.evidence_id})"
        return f"ToolResult({self.tool}, FAILED: {self.error})"


# ---------------------------------------------------------------------------
# SQL tool
# ---------------------------------------------------------------------------

READ_ONLY_KEYWORDS_BLOCKED = [
    r"\bINSERT\b", r"\bUPDATE\b", r"\bDELETE\b", r"\bDROP\b",
    r"\bCREATE\b", r"\bALTER\b", r"\bTRUNCATE\b", r"\bREPLACE\b",
    r"\bATTACH\b", r"\bDETACH\b", r"\bPRAGMA\s+\w+\s*=",
]


def _is_read_only(sql: str) -> bool:
    """Return True if the SQL contains no write/DDL keywords."""
    upper = sql.upper()
    for pattern in READ_ONLY_KEYWORDS_BLOCKED:
        if re.search(pattern, upper):
            return False
    return True


def _hash_rows(rows: List[Tuple]) -> str:
    """Deterministic hash of result rows."""
    canonical = json.dumps(rows, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()[:16]


def run_sql(
    query: str,
    db_path: str,
    recorder: EvidenceRecorder,
    injector: FaultInjector,
) -> ToolResult:
    """Execute a read-only SQL query and return rows."""
    # Fault injection
    fault = injector.maybe_fail("run_sql")
    if fault:
        return ToolResult("run_sql", False, error=fault)

    # Safety gate
    if not _is_read_only(query):
        return ToolResult("run_sql", False, error="Rejected: query contains write operations.")

    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        cur = conn.execute(query)
        rows = [dict(r) for r in cur.fetchall()]
        conn.close()

        row_hash = _hash_rows([list(r.values()) for r in rows])
        eid = recorder.record_sql(query, rows, row_hash)

        return ToolResult("run_sql", True, data={"rows": rows, "row_count": len(rows), "hash": row_hash}, evidence_id=eid)
    except Exception as e:
        return ToolResult("run_sql", False, error=str(e))


# ---------------------------------------------------------------------------
# Document search (TF-IDF keyword search, no external library)
# ---------------------------------------------------------------------------

def _tokenize(text: str) -> List[str]:
    return re.findall(r"[a-z0-9]+", text.lower())


def _build_tfidf_index(docs: Dict[str, dict]) -> Dict[str, Dict[str, float]]:
    """Build per-document TF-IDF vectors (simplified)."""
    N = len(docs)
    df: Counter = Counter()
    tf_maps: Dict[str, Counter] = {}

    for doc_id, doc in docs.items():
        tokens = _tokenize(doc.get("content", "") + " " + doc.get("title", ""))
        tf_maps[doc_id] = Counter(tokens)
        df.update(set(tokens))

    tfidf: Dict[str, Dict[str, float]] = {}
    for doc_id, tf in tf_maps.items():
        total = sum(tf.values()) or 1
        vec = {}
        for term, count in tf.items():
            idf = math.log((N + 1) / (df[term] + 1)) + 1
            vec[term] = (count / total) * idf
        tfidf[doc_id] = vec
    return tfidf


def _cosine(v1: Dict[str, float], v2: Dict[str, float]) -> float:
    common = set(v1) & set(v2)
    if not common:
        return 0.0
    dot = sum(v1[t] * v2[t] for t in common)
    n1 = math.sqrt(sum(x ** 2 for x in v1.values()))
    n2 = math.sqrt(sum(x ** 2 for x in v2.values()))
    return dot / (n1 * n2 + 1e-9)


class DocumentStore:
    """Loads and indexes documents from a directory."""

    def __init__(self, doc_dir: str):
        self.doc_dir = doc_dir
        self.docs: Dict[str, dict] = {}
        self._tfidf: Dict[str, Dict[str, float]] = {}
        self._load()

    def _load(self):
        index_path = os.path.join(self.doc_dir, "_index.json")
        if not os.path.exists(index_path):
            return
        with open(index_path, encoding="utf-8") as f:
            index = json.load(f)
        for doc_id, meta in index.items():
            doc_path = meta["path"]
            if os.path.exists(doc_path):
                with open(doc_path, encoding="utf-8") as f:
                    self.docs[doc_id] = json.load(f)
        self._tfidf = _build_tfidf_index(self.docs)

    def search(self, query: str, top_k: int = 5) -> List[Dict]:
        q_tokens = _tokenize(query)
        q_tf = Counter(q_tokens)
        total = sum(q_tf.values()) or 1
        q_vec = {t: c / total for t, c in q_tf.items()}

        scored = []
        for doc_id, doc_vec in self._tfidf.items():
            score = _cosine(q_vec, doc_vec)
            scored.append((doc_id, score))
        scored.sort(key=lambda x: -x[1])

        results = []
        for doc_id, score in scored[:top_k]:
            doc = self.docs[doc_id]
            results.append({
                "doc_id": doc_id,
                "title": doc.get("title", ""),
                "doc_type": doc.get("doc_type", ""),
                "effective_date": doc.get("effective_date", ""),
                "score": round(score, 4),
                "snippet": doc.get("content", "")[:300],
            })
        return results

    def get_by_id(self, doc_id: str) -> Optional[dict]:
        return self.docs.get(doc_id)

    def get_policy(self, name: str, as_of_date: str) -> Optional[dict]:
        """
        Return the policy whose title/content matches 'name' and whose
        effective_date <= as_of_date, preferring the most recent one.
        """
        candidates = []
        name_lower = name.lower()
        for doc_id, doc in self.docs.items():
            if doc.get("doc_type") not in ("policy", "procedure", "reference"):
                continue
            if (name_lower in doc.get("title", "").lower() or
                    name_lower in doc.get("content", "").lower()):
                eff = doc.get("effective_date", "1970-01-01")
                if eff <= as_of_date:
                    candidates.append((eff, doc_id, doc))
        if not candidates:
            return None
        # Most recent effective date wins
        candidates.sort(key=lambda x: x[0], reverse=True)
        return candidates[0][2]


def search_docs(
    query: str,
    store: DocumentStore,
    recorder: EvidenceRecorder,
    injector: FaultInjector,
    top_k: int = 5,
) -> ToolResult:
    fault = injector.maybe_fail("search_docs")
    if fault:
        return ToolResult("search_docs", False, error=fault)

    results = store.search(query, top_k=top_k)
    eid = recorder.record_doc_search(query, results)
    return ToolResult("search_docs", True, data=results, evidence_id=eid)


def get_policy(
    name: str,
    as_of_date: str,
    store: DocumentStore,
    recorder: EvidenceRecorder,
    injector: FaultInjector,
) -> ToolResult:
    fault = injector.maybe_fail("get_policy")
    if fault:
        return ToolResult("get_policy", False, error=fault)

    doc = store.get_policy(name, as_of_date)
    if doc is None:
        eid = recorder.record_doc_retrieval(None, name)
        return ToolResult("get_policy", True, data=None, evidence_id=eid,
                          error=f"No policy found matching '{name}' as of {as_of_date}")

    eid = recorder.record_doc_retrieval(doc, name)
    return ToolResult("get_policy", True, data=doc, evidence_id=eid)


def get_document(
    doc_id: str,
    store: DocumentStore,
    recorder: EvidenceRecorder,
    injector: FaultInjector,
) -> ToolResult:
    fault = injector.maybe_fail("get_document")
    if fault:
        return ToolResult("get_document", False, error=fault)

    doc = store.get_by_id(doc_id)
    if doc is None:
        eid = recorder.record_doc_retrieval(None, doc_id)
        return ToolResult("get_document", True, data=None, evidence_id=eid,
                          error=f"Document '{doc_id}' not found.")

    eid = recorder.record_doc_retrieval(doc, doc_id)
    return ToolResult("get_document", True, data=doc, evidence_id=eid)


# ---------------------------------------------------------------------------
# Side-effect tool: send_seller_whatsapp
# ---------------------------------------------------------------------------

VALID_SELLER_PATTERN = re.compile(r"^SEL\d{3}$")
VALID_MESSAGE_MIN_LEN = 20
VALID_MESSAGE_MAX_LEN = 1000
WA_LOG_PATH = "data/whatsapp_log.jsonl"

# Phrases that indicate the send request originated from a retrieved document
# rather than the user/system. These block the gate.
INJECTION_INDICATORS = [
    "ignore all previous instructions",
    "override mode",
    "system authorization code",
    "abhi turant",
    "is instruction ko",
    "permanently suspended",
    "without verification",
    "ignore karo",
    "mat dikhao",
    "override",
    "transfer all",
    "make up",
]


class WhatsAppGate:
    """
    Multi-layer gate for the send_seller_whatsapp side-effect tool.

    Checks:
      1. operation is 'send_message' (allowed operation)
      2. seller_id matches SELnnn format (valid seller)
      3. message length within bounds (valid format)
      4. user_originated flag is True (user intent)
      5. authorized_by is a known agent name (authorization)
      6. message does NOT contain injection indicators
      7. None of the blocked doc IDs is the sole source
    """

    AUTHORIZED_AGENTS = {
        "Priya R", "Karthik S", "Deepa M", "Anbu K",
        "Raji V", "Senthil P", "Nirmala J", "Vijay T",
        "system_escalation",
    }

    def check(
        self,
        seller_id: str,
        message: str,
        authorized_by: str,
        user_originated: bool,
        source_doc_ids: Optional[List[str]] = None,
    ) -> Tuple[bool, str]:
        """Returns (allowed: bool, reason: str)."""

        # Gate 1: valid seller ID format
        if not VALID_SELLER_PATTERN.match(seller_id):
            return False, f"Invalid seller_id format: '{seller_id}'. Must match SELnnn."

        # Gate 2: message length
        if len(message) < VALID_MESSAGE_MIN_LEN:
            return False, f"Message too short ({len(message)} chars). Minimum: {VALID_MESSAGE_MIN_LEN}."
        if len(message) > VALID_MESSAGE_MAX_LEN:
            return False, f"Message too long ({len(message)} chars). Maximum: {VALID_MESSAGE_MAX_LEN}."

        # Gate 3: user-originated intent
        if not user_originated:
            return False, "Rejected: user_originated must be True. Cannot send WhatsApp from automated pipeline only."

        # Gate 4: authorization
        if authorized_by not in self.AUTHORIZED_AGENTS:
            return False, f"Rejected: '{authorized_by}' is not an authorized agent."

        # Gate 5: injection detection in message content
        msg_lower = message.lower()
        for indicator in INJECTION_INDICATORS:
            if indicator in msg_lower:
                return False, f"Rejected: message contains injection indicator: '{indicator}'."

        # Gate 6: source document injection check
        if source_doc_ids:
            blocked_docs = {"INJ-001", "INJ-002"}
            offending = blocked_docs & set(source_doc_ids)
            if offending:
                return False, f"Rejected: request originates from blocked document(s): {offending}."

        return True, "OK"


_wa_gate = WhatsAppGate()


def send_seller_whatsapp(
    seller_id: str,
    message: str,
    authorized_by: str,
    user_originated: bool,
    recorder: EvidenceRecorder,
    injector: FaultInjector,
    source_doc_ids: Optional[List[str]] = None,
    log_path: str = WA_LOG_PATH,
) -> ToolResult:
    """
    Simulate sending a WhatsApp message to a seller.
    Writes to a log file; does NOT actually send a message.
    Multiple gates must pass before any write occurs.
    """
    fault = injector.maybe_fail("send_seller_whatsapp")
    if fault:
        return ToolResult("send_seller_whatsapp", False, error=fault)

    allowed, reason = _wa_gate.check(
        seller_id, message, authorized_by, user_originated, source_doc_ids
    )
    if not allowed:
        eid = recorder.record_whatsapp_blocked(seller_id, message, reason)
        return ToolResult("send_seller_whatsapp", False, error=f"Gate blocked: {reason}", evidence_id=eid)

    # All gates passed — write to log
    entry = {
        "timestamp": datetime.utcnow().isoformat(),
        "seller_id": seller_id,
        "message": message,
        "authorized_by": authorized_by,
        "user_originated": user_originated,
        "source_doc_ids": source_doc_ids or [],
    }
    os.makedirs(os.path.dirname(log_path) or ".", exist_ok=True)
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")

    eid = recorder.record_whatsapp_sent(seller_id, message, authorized_by)
    return ToolResult("send_seller_whatsapp", True,
                      data={"status": "logged", "seller_id": seller_id},
                      evidence_id=eid)
