"""
generate_db.py - Generate fictional SQLite database for the Refund War Room agent.

Usage:
    python scripts/generate_db.py --out data/refund.db --seed 42

Tables:
    sellers  - seller accounts
    orders   - purchase orders
    refunds  - refund requests and their status
    audit_log - immutable record of state changes

All data is FICTIONAL. No real customer or company data.
"""

import argparse
import hashlib
import json
import os
import random
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path


SEED = 42
random.seed(SEED)

# ---------------------------------------------------------------------------
# Reference data
# ---------------------------------------------------------------------------

ZONES = ["Madurai", "Chennai", "Coimbatore", "Tirunelveli", "Salem",
         "Thanjavur", "Vellore", "Erode", "Tiruppur", "Dindigul"]

PINCODE_TIERS = {
    "Madurai": "T1", "Chennai": "T1", "Coimbatore": "T1",
    "Tirunelveli": "T2", "Salem": "T2", "Thanjavur": "T2",
    "Vellore": "T2", "Erode": "T3", "Tiruppur": "T3", "Dindigul": "T3",
}

PAYMENT_MODES = ["COD", "UPI", "NetBanking", "CreditCard", "DebitCard"]

CATEGORIES = ["Saree", "Kurta", "Silk", "Cotton", "Bridal", "Kids", "Accessories"]

REFUND_REASONS = [
    "wrong_item_delivered", "damaged_product", "size_mismatch",
    "quality_issue", "late_delivery", "duplicate_order",
    "changed_mind", "missing_item",
]

REFUND_STATUSES = ["pending", "approved", "rejected", "processing", "completed", "escalated"]

SELLER_NAMES = [
    "Meenakshi Silks", "Tamil Weaves", "Heritage Textiles", "Madurai Mart",
    "Chennai Crafts", "Kovai Fabrics", "Kaveri Collections", "Thangam Stores",
    "Ponni Sarees", "Kumari Fashion", "Velankanni Silks", "Raja Traders",
    "Lakshmi Emporium", "Sundaram Weaves", "Mullai Collections",
]

AGENT_NAMES = [
    "Priya R", "Karthik S", "Deepa M", "Anbu K", "Raji V",
    "Senthil P", "Nirmala J", "Vijay T",
]


def rand_date(start_str: str, end_str: str) -> str:
    start = datetime.strptime(start_str, "%Y-%m-%d")
    end = datetime.strptime(end_str, "%Y-%m-%d")
    delta = (end - start).days
    d = start + timedelta(days=random.randint(0, delta))
    return d.strftime("%Y-%m-%d")


def rand_datetime(start_str: str, end_str: str) -> str:
    d = rand_date(start_str, end_str)
    h = random.randint(8, 22)
    m = random.randint(0, 59)
    return f"{d} {h:02d}:{m:02d}:00"


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

SCHEMA = """
CREATE TABLE IF NOT EXISTS sellers (
    seller_id    TEXT PRIMARY KEY,
    name         TEXT NOT NULL,
    zone         TEXT NOT NULL,
    category     TEXT NOT NULL,
    rating       REAL,
    active       INTEGER DEFAULT 1,
    joined_date  TEXT
);

CREATE TABLE IF NOT EXISTS orders (
    order_id       TEXT PRIMARY KEY,
    seller_id      TEXT NOT NULL,
    customer_id    TEXT NOT NULL,
    order_date     TEXT NOT NULL,
    delivery_date  TEXT,
    zone           TEXT NOT NULL,
    pincode_tier   TEXT NOT NULL,
    payment_mode   TEXT NOT NULL,
    amount         REAL NOT NULL,
    category       TEXT,
    status         TEXT DEFAULT 'delivered',
    FOREIGN KEY (seller_id) REFERENCES sellers(seller_id)
);

CREATE TABLE IF NOT EXISTS refunds (
    refund_id        TEXT PRIMARY KEY,
    order_id         TEXT NOT NULL,
    seller_id        TEXT NOT NULL,
    requested_date   TEXT NOT NULL,
    processed_date   TEXT,
    reason           TEXT NOT NULL,
    status           TEXT NOT NULL,
    amount           REAL NOT NULL,
    agent_id         TEXT,
    notes            TEXT,
    escalated        INTEGER DEFAULT 0,
    zone             TEXT,
    pincode_tier     TEXT,
    payment_mode     TEXT,
    FOREIGN KEY (order_id)   REFERENCES orders(order_id),
    FOREIGN KEY (seller_id)  REFERENCES sellers(seller_id)
);

CREATE TABLE IF NOT EXISTS audit_log (
    log_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_type  TEXT NOT NULL,
    entity_id    TEXT NOT NULL,
    action       TEXT NOT NULL,
    old_value    TEXT,
    new_value    TEXT,
    changed_by   TEXT,
    changed_at   TEXT NOT NULL
);
"""


# ---------------------------------------------------------------------------
# Data generation
# ---------------------------------------------------------------------------

def generate(n_sellers=15, n_orders=500, n_refunds=300, seed=SEED):
    random.seed(seed)

    # --- Sellers ---
    sellers = []
    for i, name in enumerate(SELLER_NAMES[:n_sellers]):
        zone = random.choice(ZONES)
        sellers.append({
            "seller_id": f"SEL{i+1:03d}",
            "name": name,
            "zone": zone,
            "category": random.choice(CATEGORIES),
            "rating": round(random.uniform(2.5, 5.0), 1),
            "active": 1 if random.random() > 0.1 else 0,
            "joined_date": rand_date("2020-01-01", "2023-06-01"),
        })

    # --- Orders ---
    orders = []
    for i in range(n_orders):
        seller = random.choice(sellers)
        zone = seller["zone"]
        order_date = rand_date("2023-01-01", "2024-09-30")
        delivery_days = random.randint(2, 14)
        delivery_date_dt = datetime.strptime(order_date, "%Y-%m-%d") + timedelta(days=delivery_days)
        orders.append({
            "order_id": f"ORD{i+1:05d}",
            "seller_id": seller["seller_id"],
            "customer_id": f"CUST{random.randint(1000, 9999)}",
            "order_date": order_date,
            "delivery_date": delivery_date_dt.strftime("%Y-%m-%d"),
            "zone": zone,
            "pincode_tier": PINCODE_TIERS[zone],
            "payment_mode": random.choice(PAYMENT_MODES),
            "amount": round(random.uniform(150, 12000), 2),
            "category": seller["category"],
            "status": random.choice(["delivered", "delivered", "delivered", "returned", "lost"]),
        })

    # --- Refunds ---
    refunds = []
    sampled_orders = random.sample(orders, min(n_refunds, len(orders)))
    for i, order in enumerate(sampled_orders):
        req_date_dt = datetime.strptime(order["delivery_date"], "%Y-%m-%d") + timedelta(
            days=random.randint(0, 30)
        )
        req_date = req_date_dt.strftime("%Y-%m-%d")

        status = random.choices(
            REFUND_STATUSES,
            weights=[20, 25, 15, 10, 25, 5],
        )[0]

        processed_date = None
        if status in ("approved", "rejected", "completed"):
            processed_date_dt = req_date_dt + timedelta(days=random.randint(1, 10))
            processed_date = processed_date_dt.strftime("%Y-%m-%d")

        reason = random.choice(REFUND_REASONS)
        amount = round(order["amount"] * random.uniform(0.5, 1.0), 2)

        # Inject some missing fields (notes=None for ~30%)
        notes = None
        if random.random() > 0.3:
            notes = random.choice([
                "Customer called twice", "Photo evidence provided",
                "Seller disputed claim", "Auto-approved by system",
                "Pending seller confirmation", "Bank transfer initiated",
            ])

        escalated = 1 if status == "escalated" else 0

        refunds.append({
            "refund_id": f"REF{i+1:05d}",
            "order_id": order["order_id"],
            "seller_id": order["seller_id"],
            "requested_date": req_date,
            "processed_date": processed_date,
            "reason": reason,
            "status": status,
            "amount": amount,
            "agent_id": random.choice(AGENT_NAMES),
            "notes": notes,
            "escalated": escalated,
            "zone": order["zone"],
            "pincode_tier": order["pincode_tier"],
            "payment_mode": order["payment_mode"],
        })

    # --- Audit log entries ---
    audit_entries = []
    for ref in refunds[:50]:  # Audit the first 50 refunds
        audit_entries.append({
            "entity_type": "refund",
            "entity_id": ref["refund_id"],
            "action": "status_change",
            "old_value": "pending",
            "new_value": ref["status"],
            "changed_by": ref["agent_id"] or "system",
            "changed_at": rand_datetime("2023-01-01", "2024-09-30"),
        })

    return sellers, orders, refunds, audit_entries


def write_db(db_path: str, sellers, orders, refunds, audit_entries):
    os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
    if os.path.exists(db_path):
        os.remove(db_path)

    conn = sqlite3.connect(db_path)
    conn.executescript(SCHEMA)

    conn.executemany(
        "INSERT INTO sellers VALUES (:seller_id, :name, :zone, :category, :rating, :active, :joined_date)",
        sellers,
    )
    conn.executemany(
        "INSERT INTO orders VALUES (:order_id, :seller_id, :customer_id, :order_date, "
        ":delivery_date, :zone, :pincode_tier, :payment_mode, :amount, :category, :status)",
        orders,
    )
    conn.executemany(
        "INSERT INTO refunds VALUES (:refund_id, :order_id, :seller_id, :requested_date, "
        ":processed_date, :reason, :status, :amount, :agent_id, :notes, :escalated, "
        ":zone, :pincode_tier, :payment_mode)",
        refunds,
    )
    conn.executemany(
        "INSERT INTO audit_log (entity_type, entity_id, action, old_value, new_value, changed_by, changed_at) "
        "VALUES (:entity_type, :entity_id, :action, :old_value, :new_value, :changed_by, :changed_at)",
        audit_entries,
    )
    conn.commit()
    conn.close()
    print(f"Database written -> {db_path}")
    print(f"  sellers: {len(sellers)}, orders: {len(orders)}, refunds: {len(refunds)}, audit: {len(audit_entries)}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--out",  default="data/refund.db")
    parser.add_argument("--seed", type=int, default=SEED)
    args = parser.parse_args()

    sellers, orders, refunds, audit = generate(seed=args.seed)
    write_db(args.out, sellers, orders, refunds, audit)
    print("Done.")
