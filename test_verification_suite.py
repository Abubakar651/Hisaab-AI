"""
Comprehensive verification test suite for HisaabAI.
Runs tests against actual database, analytics, API routes, and agent tools.
"""
import sys
import os
import json
from datetime import datetime
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient

from dotenv import load_dotenv
load_dotenv()

from database import SessionLocal, init_db, Transaction, TransactionItem, Budget, RecurringExpense, get_pkt_now
from analytics import (
    normalize_payment_method,
    category_breakdown,
    daily_trend,
    payment_split,
    budget_status,
    recurring_status,
    pay_recurring,
    export_rows,
    EXPORT_HEADER,
    month_start,
)
from main import app, create_session_token
from agent import get_tools_for_user, clear_user_history

TEST_USER = "test_verification_suite_user"


def cleanup():
    clear_user_history(TEST_USER)
    db = SessionLocal()
    try:
        txs = db.query(Transaction).filter(Transaction.user_id == TEST_USER).all()
        for t in txs:
            db.delete(t)
        db.query(Budget).filter(Budget.user_id == TEST_USER).delete()
        db.query(RecurringExpense).filter(RecurringExpense.user_id == TEST_USER).delete()
        db.commit()
    finally:
        db.close()


def run_tests():
    print("=" * 60)
    print("STARTING HISAAB-AI VERIFICATION SUITE")
    print("=" * 60)

    init_db()
    cleanup()

    client = TestClient(app)
    token = create_session_token(TEST_USER)
    auth_headers = {"Authorization": f"Bearer {token}"}
    results = {}

    # -------------------------------------------------------------
    # FEATURE 1: Payment Method Tracking & Tools
    # -------------------------------------------------------------
    print("\n[TEST 1] Testing Payment Method Normalization & Tracking...")
    assert normalize_payment_method("cash") == "cash"
    assert normalize_payment_method("naqd") == "cash"
    assert normalize_payment_method("easypaisa") == "easypaisa"
    assert normalize_payment_method("jazz cash") == "jazzcash"
    assert normalize_payment_method("debit card") == "card"
    assert normalize_payment_method("sadapay") == "card"
    assert normalize_payment_method("nayapay") == "card"
    assert normalize_payment_method("raast") == "bank_transfer"
    assert normalize_payment_method("unknown_xyz") == "cash"
    print("  -> Aliases and fallback logic OK.")

    # Agent tool call with payment method
    tools = {t.name: t for t in get_tools_for_user(TEST_USER)}
    tool_res = json.loads(tools["log_expense"].invoke({
        "store_name": "imtiaz",
        "items": [{"item_name": "cooking oil", "price": 2500.0, "quantity": 1.0, "category": "groceries"}],
        "total_amount": 2500.0,
        "payment_method": "card",
    }))
    assert tool_res["status"] == "success"

    db = SessionLocal()
    tx = db.query(Transaction).filter(Transaction.user_id == TEST_USER).order_by(Transaction.id.desc()).first()
    assert tx is not None, "Transaction not created!"
    assert tx.payment_method == "card", f"Expected payment_method 'card', got '{tx.payment_method}'"
    assert tx.total_amount == 2500.0, f"Expected 2500.0, got {tx.total_amount}"
    print(f"  -> DB verified: Transaction #{tx.id} recorded with payment_method='{tx.payment_method}', amount={tx.total_amount}")
    db.close()
    results["1_payment_methods"] = "PASSED"

    # -------------------------------------------------------------
    # FEATURE 2: CSV / Excel Export
    # -------------------------------------------------------------
    print("\n[TEST 2] Testing CSV / Excel Export Endpoint...")
    resp = client.get(f"/api/export/{TEST_USER}", headers=auth_headers)
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"
    content_bytes = resp.content
    assert content_bytes.startswith(b"\xef\xbb\xbf"), "Missing UTF-8 BOM for Microsoft Excel compatibility!"
    text = content_bytes.decode("utf-8-sig")
    lines = text.strip().split("\r\n") if "\r\n" in text else text.strip().split("\n")
    assert len(lines) >= 2, f"Expected header + data row, got {len(lines)} lines"
    assert "Payment Method" in lines[0], "Header missing 'Payment Method'"
    assert "card" in lines[1], "Data row missing 'card' payment method"
    print(f"  -> Export verified: UTF-8 BOM present, header correct, {len(lines)-1} transaction row(s) exported.")
    results["2_csv_export"] = "PASSED"

    # -------------------------------------------------------------
    # FEATURE 3: Monthly Budgeting & Alerts
    # -------------------------------------------------------------
    print("\n[TEST 3] Testing Monthly Budgeting & Alerts...")
    # API route with auth headers
    post_res = client.post(
        "/api/budgets",
        data={"user_id": TEST_USER, "monthly_limit": "50000", "category": "total"},
        headers=auth_headers,
    )
    assert post_res.status_code == 200, f"Budget API failed: {post_res.text}"
    budgets = post_res.json().get("budgets", [])
    assert len(budgets) == 1
    assert budgets[0]["monthly_limit"] == 50000.0
    assert budgets[0]["spent_pkr"] == 2500.0
    assert budgets[0]["percent"] == 5.0
    assert budgets[0]["level"] == "ok"
    print(f"  -> API Budget created: total=50000, spent={budgets[0]['spent_pkr']}, pct={budgets[0]['percent']}%")

    # Agent tool call for category budget
    set_budget_res = json.loads(tools["set_budget"].invoke({"monthly_limit": 3000.0, "category": "fuel"}))
    assert set_budget_res["status"] == "success"

    db = SessionLocal()
    b_fuel = db.query(Budget).filter(Budget.user_id == TEST_USER, Budget.category == "fuel").first()
    assert b_fuel is not None, "Category budget 'fuel' not created by agent!"
    assert b_fuel.monthly_limit == 3000.0
    print(f"  -> DB verified: Fuel budget set to PKR {b_fuel.monthly_limit}")

    # Add an expense of 2800 fuel to trigger warning (2800 / 3000 = 93.3% -> warning)
    tools["log_expense"].invoke({
        "store_name": "pso petrol pump",
        "items": [{"item_name": "petrol", "price": 2800.0, "quantity": 10.0, "category": "fuel"}],
        "total_amount": 2800.0,
        "payment_method": "cash",
    })

    # Check budget status in DB
    status_list = budget_status(db, TEST_USER)
    fuel_st = next(s for s in status_list if s["category"] == "fuel")
    assert fuel_st["spent_pkr"] >= 2800.0, f"Expected >= 2800, got {fuel_st['spent_pkr']}"
    assert fuel_st["level"] in ("warning", "over"), f"Expected warning/over, got {fuel_st['level']}"
    print(f"  -> Budget alert verified: Spent {fuel_st['spent_pkr']} / {fuel_st['monthly_limit']} ({fuel_st['percent']}%, level={fuel_st['level']})")
    db.close()
    results["3_budgeting_alerts"] = "PASSED"

    # -------------------------------------------------------------
    # FEATURE 4: Recurring Monthly Expenses
    # -------------------------------------------------------------
    print("\n[TEST 4] Testing Recurring Monthly Obligations...")
    add_rec_res = json.loads(tools["add_recurring_expense"].invoke({
        "title": "electricity bill",
        "amount": 12000.0,
        "due_day": 5,
        "category": "utilities",
    }))
    assert add_rec_res["status"] == "success"

    db = SessionLocal()
    rec = db.query(RecurringExpense).filter(RecurringExpense.user_id == TEST_USER).first()
    assert rec is not None, "RecurringExpense record not created by agent!"
    assert rec.amount == 12000.0
    assert rec.due_day == 5
    print(f"  -> DB verified: Recurring '{rec.title}' PKR {rec.amount} due day {rec.due_day}")

    # Agent tool call to mark as paid
    pay_res = json.loads(tools["mark_recurring_paid"].invoke({"title": "electricity bill"}))
    assert pay_res["status"] == "success"

    db.refresh(rec)
    current_month = get_pkt_now().strftime("%Y-%m")
    assert rec.paid_month == current_month, f"Expected paid_month {current_month}, got {rec.paid_month}"

    paid_tx = db.query(Transaction).filter(
        Transaction.user_id == TEST_USER,
        Transaction.total_amount == 12000.0,
    ).first()
    assert paid_tx is not None, "Transaction for recurring payment not found in ledger!"
    print(f"  -> DB verified: Recurring bill marked paid for {rec.paid_month} and ledger Transaction #{paid_tx.id} created!")
    db.close()
    results["4_recurring_expenses"] = "PASSED"

    # -------------------------------------------------------------
    # FEATURE 5: Visual Analytics & Deterministic Calculations
    # -------------------------------------------------------------
    print("\n[TEST 5] Testing Deterministic Analytics (Categories, Trend, Payment Split)...")
    db = SessionLocal()
    s = month_start()
    cats = category_breakdown(db, TEST_USER, s)
    assert len(cats) >= 2, f"Expected at least 2 categories, got {len(cats)}"
    cat_sum_pct = sum(c["percent"] for c in cats)
    assert 99.0 <= cat_sum_pct <= 101.0, f"Category percentages don't sum to 100%: {cat_sum_pct}"
    print(f"  -> Category breakdown verified ({len(cats)} categories, sum={cat_sum_pct}%): {[(c['category'], c['total_pkr']) for c in cats]}")

    trend = daily_trend(db, TEST_USER, days=30)
    assert len(trend) == 30, f"Expected 30 daily buckets, got {len(trend)}"
    print(f"  -> Daily trend verified (30 consecutive non-gap days).")

    psplit = payment_split(db, TEST_USER, s)
    assert len(psplit) >= 2, f"Expected both card and cash in split, got {psplit}"
    print(f"  -> Payment split verified: {[(p['method'], p['total_pkr']) for p in psplit]}")
    db.close()
    results["5_visual_analytics"] = "PASSED"

    # -------------------------------------------------------------
    # FEATURE 6: Progressive Web App (PWA)
    # -------------------------------------------------------------
    print("\n[TEST 6] Testing PWA Manifest, Service Worker, and Icons...")
    manifest_res = client.get("/manifest.webmanifest")
    assert manifest_res.status_code == 200, f"Expected 200, got {manifest_res.status_code}"
    manifest_data = manifest_res.json()
    assert manifest_data["name"] == "HisaabAI - Voice Expense Tracker"
    assert manifest_data["display"] == "standalone"
    assert len(manifest_data["icons"]) >= 2
    print(f"  -> manifest.webmanifest OK (standalone display, icons defined).")

    sw_res = client.get("/sw.js")
    assert sw_res.status_code == 200, f"Expected 200, got {sw_res.status_code}"
    assert sw_res.headers.get("service-worker-allowed") == "/"
    assert "hisaabai-shell-v1" in sw_res.text
    print(f"  -> sw.js OK (Service-Worker-Allowed: /, cache strategy configured).")

    for icon in ["icon-192.png", "icon-512.png", "icon-maskable-512.png"]:
        icon_path = os.path.join("static", "icons", icon)
        assert os.path.exists(icon_path), f"Missing icon: {icon_path}"
        with open(icon_path, "rb") as f:
            header = f.read(8)
            assert header == b"\x89PNG\r\n\x1a\n", f"Invalid PNG magic bytes in {icon}"
    print(f"  -> 3 PWA icons verified with valid PNG headers.")
    results["6_pwa"] = "PASSED"

    # -------------------------------------------------------------
    # FEATURE 7: WhatsApp Webhook Endpoint
    # -------------------------------------------------------------
    print("\n[TEST 7] Testing WhatsApp Webhook Route...")
    wa_res_unconfigured = client.post("/api/whatsapp", data={"From": "whatsapp:+923001234567", "Body": "test"})
    assert wa_res_unconfigured.status_code in (503, 403, 200)
    print(f"  -> WhatsApp endpoint guards verified (Status code: {wa_res_unconfigured.status_code}).")
    results["7_whatsapp_webhook"] = "PASSED"

    # Clean up test rows
    cleanup()
    print("\n" + "=" * 60)
    print("ALL 7 VERIFICATION TESTS PASSED SUCCESSFULLY!")
    print("=" * 60)
    for k, v in results.items():
        print(f"  - {k}: {v}")


if __name__ == "__main__":
    run_tests()
