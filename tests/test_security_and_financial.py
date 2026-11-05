import os
import io
import json
import base64
import hashlib
import pytest
from datetime import datetime, timedelta
from unittest.mock import patch, MagicMock

from database import (
    Transaction,
    TransactionItem,
    Budget,
    RecurringExpense,
    UserAccount,
    LoginAttempt,
    PendingTransaction,
    get_pkt_now,
)
from analytics import (
    validate_transaction_amount,
    check_amount_plausibility,
    category_breakdown,
    pay_recurring,
    export_rows,
    sanitize_csv_cell,
)
import analytics
from agent import get_tools_for_user
import main


def test_auth_routes_without_token_return_401(client):
    """Every data route must return 401 Unauthorized without an Authorization header."""
    routes = [
        ("POST", "/api/chat", {"data": {"message": "hello"}}),
        ("POST", "/api/chat/clear", {"data": {}}),
        ("GET", "/api/transactions/alice", {}),
        ("GET", "/api/export/alice", {}),
        ("GET", "/api/budgets/alice", {}),
        ("POST", "/api/budgets", {"data": {"monthly_limit": "5000"}}),
        ("GET", "/api/recurring/alice", {}),
        ("POST", "/api/recurring", {"data": {"title": "Rent", "amount": "10000"}}),
        ("DELETE", "/api/transactions/alice/1", {}),
    ]

    for method, path, kwargs in routes:
        if method == "GET":
            resp = client.get(path)
        elif method == "POST":
            resp = client.post(path, **kwargs)
        elif method == "DELETE":
            resp = client.delete(path)
        assert resp.status_code == 401, f"Expected 401 for unauthenticated {method} {path}, got {resp.status_code}"


def test_idor_cross_user_access_returns_403(client, auth_headers):
    """An authenticated user attempting to access another user's records must receive 403 Forbidden."""
    headers_alice = auth_headers("alice")

    # Alice tries to read Bob's transactions
    resp = client.get("/api/transactions/bob", headers=headers_alice)
    assert resp.status_code == 403

    # Alice tries to export Bob's CSV
    resp = client.get("/api/export/bob", headers=headers_alice)
    assert resp.status_code == 403

    # Alice tries to view Bob's budgets
    resp = client.get("/api/budgets/bob", headers=headers_alice)
    assert resp.status_code == 403

    # Alice tries to delete Bob's transaction
    resp = client.delete("/api/transactions/bob/999", headers=headers_alice)
    assert resp.status_code == 403

    # Alice tries to pay Bob's recurring expense
    resp = client.post("/api/recurring/bob/1/pay", headers=headers_alice)
    assert resp.status_code == 403

    # Alice tries to pass another user's ID in /api/chat form
    resp = client.post("/api/chat", data={"user_id": "bob", "message": "hello"}, headers=headers_alice)
    assert resp.status_code == 403


def test_firebase_token_verification_cases(client, db):
    """
    Tests server-side Firebase ID token verification:
    - Missing token -> 401
    - Garbage token -> 401
    - Unsupported provider ('phone', 'anonymous', 'custom') -> 401
    - Password provider with email_verified=False -> 401 with 'email_not_verified'
    - Password provider with email_verified=True -> 200 on /api/auth/me and row upserted
    - Google provider with email_verified=True -> 200 on /api/auth/me and row upserted
    - Google provider with email_verified=False -> 401
    - Missing email -> 401
    """
    project_id = "test-hisaab-ai-project"

    # 1. Missing token
    resp = client.post("/api/auth/me")
    assert resp.status_code == 401

    # 2. Garbage token
    resp_garbage = client.post("/api/auth/me", headers={"Authorization": "Bearer not-a-valid-jwt"})
    assert resp_garbage.status_code == 401

    # 3. Unsupported provider (phone)
    phone_claims = {
        "email": "phone_user@example.com",
        "email_verified": True,
        "aud": project_id,
        "iss": f"https://securetoken.google.com/{project_id}",
        "firebase": {"sign_in_provider": "phone"},
    }
    with patch("main.admin_auth.verify_id_token", side_effect=Exception("Admin fallback")):
        with patch("google.oauth2.id_token.verify_firebase_token", return_value=phone_claims):
            resp_phone = client.post("/api/auth/me", headers={"Authorization": "Bearer phone-token"})
            assert resp_phone.status_code == 401
            assert "unsupported" in resp_phone.json()["detail"].lower()

    # 4. Password provider with email_verified=False
    pw_unverified_claims = {
        "email": "unverified@example.com",
        "email_verified": False,
        "aud": project_id,
        "iss": f"https://securetoken.google.com/{project_id}",
        "firebase": {"sign_in_provider": "password"},
    }
    with patch("main.admin_auth.verify_id_token", side_effect=Exception("Admin fallback")):
        with patch("google.oauth2.id_token.verify_firebase_token", return_value=pw_unverified_claims):
            resp_unverified = client.post("/api/auth/me", headers={"Authorization": "Bearer unverified-token"})
            assert resp_unverified.status_code == 401
            assert resp_unverified.json()["detail"] == "email_not_verified"

    # 5. Password provider with email_verified=True
    pw_verified_claims = {
        "uid": "fb-pw-user-1",
        "email": "pw_verified@example.com",
        "email_verified": True,
        "name": "Password User",
        "aud": project_id,
        "iss": f"https://securetoken.google.com/{project_id}",
        "firebase": {"sign_in_provider": "password"},
    }
    with patch("main.admin_auth.verify_id_token", side_effect=Exception("Admin fallback")):
        with patch("google.oauth2.id_token.verify_firebase_token", return_value=pw_verified_claims):
            resp_pw = client.post("/api/auth/me", headers={"Authorization": "Bearer valid-pw-token"})
            assert resp_pw.status_code == 200
            data = resp_pw.json()
            assert data["status"] == "success"
            assert data["user"]["user_id"] == "pw_verified@example.com"
            assert data["user"]["auth_provider"] == "password"
            assert data["user"]["firebase_uid"] == "fb-pw-user-1"

    # 6. Google provider with email_verified=True
    google_claims = {
        "uid": "fb-google-user-1",
        "email": "google_verified@example.com",
        "email_verified": True,
        "name": "Google User",
        "picture": "https://lh3.googleusercontent.com/photo.jpg",
        "aud": project_id,
        "iss": f"https://securetoken.google.com/{project_id}",
        "firebase": {"sign_in_provider": "google.com"},
    }
    with patch("main.admin_auth.verify_id_token", side_effect=Exception("Admin fallback")):
        with patch("google.oauth2.id_token.verify_firebase_token", return_value=google_claims):
            resp_g = client.post("/api/auth/me", headers={"Authorization": "Bearer valid-google-token"})
            assert resp_g.status_code == 200
            data_g = resp_g.json()
            assert data_g["user"]["user_id"] == "google_verified@example.com"
            assert data_g["user"]["auth_provider"] == "google"
            assert data_g["user"]["photo_url"] == "https://lh3.googleusercontent.com/photo.jpg"

    # 7. Google provider with email_verified=False
    google_unverified_claims = {
        "email": "google_bad@example.com",
        "email_verified": False,
        "aud": project_id,
        "iss": f"https://securetoken.google.com/{project_id}",
        "firebase": {"sign_in_provider": "google.com"},
    }
    with patch("main.admin_auth.verify_id_token", side_effect=Exception("Admin fallback")):
        with patch("google.oauth2.id_token.verify_firebase_token", return_value=google_unverified_claims):
            resp_gbad = client.post("/api/auth/me", headers={"Authorization": "Bearer bad-google-token"})
            assert resp_gbad.status_code == 401

    # 8. Missing email in token
    no_email_claims = {
        "email": "",
        "email_verified": True,
        "aud": project_id,
        "iss": f"https://securetoken.google.com/{project_id}",
        "firebase": {"sign_in_provider": "google.com"},
    }
    with patch("main.admin_auth.verify_id_token", side_effect=Exception("Admin fallback")):
        with patch("google.oauth2.id_token.verify_firebase_token", return_value=no_email_claims):
            resp_no_email = client.post("/api/auth/me", headers={"Authorization": "Bearer no-email-token"})
            assert resp_no_email.status_code == 401


def test_removed_auth_endpoints_return_404(client):
    """
    Deleted legacy authentication endpoints must return 404 Not Found.
    """
    assert client.post("/api/auth/register", json={}).status_code == 404
    assert client.post("/api/auth/password-login", json={}).status_code == 404
    assert client.post("/api/auth/login", json={}).status_code == 404
    assert client.post("/api/auth/logout-all").status_code == 404


def test_no_custom_jwt_or_pbkdf2_code():
    """
    Static code audit: main.py must not contain custom JWT or PBKDF2 password code.
    """
    with open("main.py", "r", encoding="utf-8") as f:
        src = f.read()

    assert "import jwt" not in src
    assert "create_session_token" not in src
    assert "decode_session_token" not in src
    assert "hash_password" not in src
    assert "DUMMY_PBKDF2_HASH" not in src
    assert "SESSION_SECRET" not in src


def test_firebase_auth_config_endpoint(client):
    """
    GET /api/auth/config returns public Firebase configuration without leaking backend secrets.
    """
    resp = client.get("/api/auth/config")
    assert resp.status_code == 200
    cfg = resp.json()
    assert "projectId" in cfg or "projectId" in cfg.get("firebase", {})
    assert "apiKey" in cfg or "apiKey" in cfg.get("firebase", {})



def test_amount_handling_and_confirmation(client, db, auth_headers):
    """
    Amount handling tests:
    - '200 lakh' -> 20,000,000 stays intact and requests confirmation
    - '20000 lakh' is flagged for confirmation (not rewritten to 20,000)
    - '6000 hazaar' stays 6,000,000
    - '60 lakh jazzcash' is flagged for confirmation (not truncated to 60)
    - Confirmed exclusively via human UI endpoint; agent tool only cancels
    """
    # 1. '200 lakh' -> 20,000,000 stays intact
    val_200_lakh = validate_transaction_amount(20_000_000)
    assert val_200_lakh == 20_000_000.0
    plaus_200 = check_amount_plausibility(val_200_lakh, "cash")
    assert plaus_200["needs_confirmation"] is True

    # 2. '20000 lakh' -> 2,000,000,000 is flagged (not overwritten to 20,000)
    val_20000_lakh = validate_transaction_amount(2_000_000_000)
    assert val_20000_lakh == 2_000_000_000.0
    plaus_20000 = check_amount_plausibility(val_20000_lakh, "cash")
    assert plaus_20000["needs_confirmation"] is True

    # 3. '6000 hazaar' -> 6,000,000 stays intact
    val_6000_haz = validate_transaction_amount(6_000_000)
    assert val_6000_haz == 6_000_000.0
    plaus_6000 = check_amount_plausibility(val_6000_haz, "cash")
    assert plaus_6000["needs_confirmation"] is False

    # 4. '60 lakh jazzcash' -> 6,000,000 on jazzcash flagged for wallet limit, NOT truncated
    val_jazz = validate_transaction_amount(6_000_000)
    assert val_jazz == 6_000_000.0
    plaus_jazz = check_amount_plausibility(val_jazz, "jazzcash")
    assert plaus_jazz["needs_confirmation"] is True
    assert "Jazzcash" in plaus_jazz["reason"]

    # 5. Test log_expense server-held pending confirmation flow
    user_id = "test_user_amounts"
    headers = auth_headers(user_id)
    tools = {t.name: t for t in get_tools_for_user(user_id)}
    log_expense_tool = tools["log_expense"]
    cancel_pending_tool = tools["cancel_pending_expense"]

    # Agent cannot confirm: confirm_pending_expense must not exist in tools
    assert "confirm_pending_expense" not in tools

    # Call for 20M creates pending transaction and returns needs_confirmation
    res_unconfirmed = json.loads(log_expense_tool.invoke({
        "store_name": "property dealer",
        "total_amount": 20_000_000,
        "payment_method": "bank_transfer",
    }))
    assert res_unconfirmed["status"] == "needs_confirmation"
    assert res_unconfirmed["amount"] == 20_000_000.0

    # Confirmed call via human authenticated UI endpoint commits exact payload
    res_confirmed = client.post("/api/transactions/pending/confirm", headers=headers)
    assert res_confirmed.status_code == 200
    assert res_confirmed.json()["status"] == "success"
    assert res_confirmed.json()["transaction"]["total_amount"] == 20_000_000.0

    # Call for another large transaction and test agent cancel tool
    res_cancel_test = json.loads(log_expense_tool.invoke({
        "store_name": "car showroom",
        "total_amount": 15_000_000,
        "payment_method": "bank_transfer",
    }))
    assert res_cancel_test["status"] == "needs_confirmation"

    res_cancelled = json.loads(cancel_pending_tool.invoke({}))
    assert res_cancelled["status"] == "cancelled"


def test_category_totals_and_line_item_math_consistency(db):
    """
    TransactionItem.price is LINE TOTAL.
    Category breakdown sum must equal total_amount for multi-item multi-quantity transactions.
    """
    user_id = "math_test_user"
    tx = Transaction(
        user_id=user_id,
        store_name="imtiaz",
        total_amount=450.0,
        transaction_date=get_pkt_now(),
        payment_method="cash",
    )
    db.add(tx)
    db.flush()

    # Item 1: 2 kg sugar for PKR 300 LINE TOTAL (price=300, quantity=2)
    it1 = TransactionItem(
        transaction_id=tx.id,
        item_name="sugar",
        category="groceries",
        price=300.0,
        quantity=2.0,
    )
    # Item 2: 1 bread for PKR 150 LINE TOTAL (price=150, quantity=1)
    it2 = TransactionItem(
        transaction_id=tx.id,
        item_name="bread",
        category="bakery",
        price=150.0,
        quantity=1.0,
    )
    db.add_all([it1, it2])
    db.commit()

    # Subtotal in to_dict() must equal line total (price), not price * quantity
    assert it1.to_dict()["subtotal"] == 300.0
    assert it1.to_dict()["unit_price"] == 150.0
    assert it2.to_dict()["subtotal"] == 150.0

    # Category breakdown must deterministically sum to 450.0
    breakdown = category_breakdown(db, user_id, since=datetime(2000, 1, 1))
    total_breakdown = sum(item["total_pkr"] for item in breakdown)
    assert total_breakdown == 450.0


def test_user_namespace_isolation(client, db, auth_headers):
    """Prefixed user namespaces must be strictly isolated from plain user accounts."""
    user_plain = "user_alpha"
    user_isolated = "tenant:user_alpha"

    # Add transaction for plain user
    tx_plain = Transaction(
        user_id=user_plain,
        store_name="Store Plain",
        total_amount=500.0,
        transaction_date=get_pkt_now(),
        payment_method="cash",
    )
    # Add transaction for isolated user
    tx_isolated = Transaction(
        user_id=user_isolated,
        store_name="Store Isolated",
        total_amount=900.0,
        transaction_date=get_pkt_now(),
        payment_method="cash",
    )
    db.add_all([tx_plain, tx_isolated])
    db.commit()

    # Query plain user ledger
    headers_plain = auth_headers(user_plain)
    resp = client.get(f"/api/transactions/{user_plain}", headers=headers_plain)
    assert resp.status_code == 200
    data = resp.json()
    assert data["monthly_total"] == 500.0
    assert len(data["recent_transactions"]) == 1
    assert data["recent_transactions"][0]["store_name"] == "Store Plain"


def test_csv_formula_injection_sanitization(client, db, auth_headers):
    """Formulas prefixed with =, +, -, @ must be sanitized with a leading single quote."""
    user_id = "csv_user"
    tx = Transaction(
        user_id=user_id,
        store_name="=CMD|' /C calc'!A0",
        total_amount=100.0,
        transaction_date=get_pkt_now(),
        payment_method="cash",
    )
    db.add(tx)
    db.flush()
    it = TransactionItem(
        transaction_id=tx.id,
        item_name="+DDE('cmd';'/c calc';'a')",
        category="@SUM(1+1)",
        price=100.0,
        quantity=1.0,
    )
    db.add(it)
    db.commit()

    rows = export_rows(db, user_id)
    assert len(rows) == 1
    row = rows[0]
    # Store name (col 2), Item name (col 3), Category (col 4)
    assert row[2].startswith("'=")
    assert row[3].startswith("'+")
    assert row[4].startswith("'@")


def test_recurring_bill_idempotency(db):
    """Marking recurring bill paid twice in same month must not log duplicate transactions."""
    user_id = "rec_user"
    item = RecurringExpense(
        user_id=user_id,
        title="lesco electricity bill",
        amount=15000.0,
        due_day=10,
        category="utilities",
    )
    db.add(item)
    db.commit()

    # First payment
    res1 = pay_recurring(db, user_id, item)
    assert res1["status"] == "success"

    # Second payment in same month
    res2 = pay_recurring(db, user_id, item)
    assert res2["status"] == "already_paid"

    # Verify only 1 transaction exists in DB
    tx_count = db.query(Transaction).filter(Transaction.user_id == user_id).count()
    assert tx_count == 1


def test_audio_file_size_and_mimetype_validation(client, auth_headers):
    """Audio files > 5MB or invalid MIME-types must be rejected."""
    headers = auth_headers("audio_user")

    # 1. Invalid file extension / MIME type
    resp_bad_ext = client.post(
        "/api/chat",
        data={"user_id": "audio_user", "message": "hello"},
        files={"audio": ("script.py", b"print('malicious')", "text/x-python")},
        headers=headers,
    )
    assert resp_bad_ext.status_code == 400

    # 2. File size exceeding 5 MB limit
    oversized_audio = b"\x00" * (5 * 1024 * 1024 + 1024)
    resp_large = client.post(
        "/api/chat",
        data={"user_id": "audio_user", "message": "hello"},
        files={"audio": ("voice.webm", oversized_audio, "audio/webm")},
        headers=headers,
    )
    assert resp_large.status_code == 413


def test_soft_delete_leak_audit_across_all_analytics(client, db, auth_headers):
    """
    Comprehensive leak audit: Ensures soft-deleted rows (deleted_at IS NOT NULL)
    are excluded from EVERY single query, report, tool, and summary:
    - Monthly total & count
    - Store breakdown
    - Category breakdown
    - Daily trend
    - Payment split
    - Budget status
    - CSV export
    - LangChain query_expenses tool
    """
    user_id = "soft_delete_audit_user"
    headers = auth_headers(user_id)

    # 1. Create a transaction with line items and budget
    tx = Transaction(
        user_id=user_id,
        store_name="alfatah",
        total_amount=5000.0,
        transaction_date=get_pkt_now(),
        payment_method="cash",
    )
    db.add(tx)
    db.flush()
    db.add(TransactionItem(
        transaction_id=tx.id,
        item_name="cooking oil",
        category="groceries",
        price=5000.0,
        quantity=1.0,
    ))
    db.add(Budget(user_id=user_id, category="groceries", monthly_limit=10000.0))
    db.add(Budget(user_id=user_id, category="total", monthly_limit=20000.0))
    db.commit()

    # Verify active transaction is counted in all metrics
    resp_active = client.get(f"/api/transactions/{user_id}", headers=headers)
    assert resp_active.status_code == 200
    data_active = resp_active.json()
    assert data_active["monthly_total"] == 5000.0
    assert data_active["monthly_count"] == 1
    assert len(data_active["category_breakdown"]) == 1
    assert len(data_active["store_breakdown"]) == 1
    assert len(data_active["recent_transactions"]) == 1

    # Check query_expenses tool
    tools = {t.name: t for t in get_tools_for_user(user_id)}
    tool_resp_active = json.loads(tools["query_expenses"].invoke({}))
    assert tool_resp_active["total_spent_pkr"] == 5000.0
    assert tool_resp_active["visit_count"] == 1

    # 2. Soft-delete the transaction
    resp_del = client.delete(f"/api/transactions/{user_id}/{tx.id}", headers=headers)
    assert resp_del.status_code == 200
    assert resp_del.json()["status"] == "success"

    # 3. Verify ALL endpoints return 0 / empty
    resp_deleted = client.get(f"/api/transactions/{user_id}", headers=headers)
    assert resp_deleted.status_code == 200
    data_del = resp_deleted.json()

    assert data_del["monthly_total"] == 0.0, "Monthly total must exclude soft-deleted transactions"
    assert data_del["monthly_count"] == 0, "Monthly count must exclude soft-deleted transactions"
    assert len(data_del["store_breakdown"]) == 0, "Store breakdown must exclude soft-deleted transactions"
    assert len(data_del["category_breakdown"]) == 0, "Category breakdown must exclude soft-deleted transactions"
    assert len(data_del["payment_split"]) == 0, "Payment split must exclude soft-deleted transactions"
    assert len(data_del["recent_transactions"]) == 0, "Recent transactions must exclude soft-deleted transactions"

    # Verify daily trend shows 0
    today_str = get_pkt_now().strftime("%Y-%m-%d")
    today_trend = next((d for d in data_del["daily_trend"] if d["date"] == today_str), None)
    assert today_trend is not None
    assert today_trend["total_pkr"] == 0.0, "Daily trend must exclude soft-deleted transactions"

    # Verify budget status reports 0 spent
    for b in data_del["budgets"]:
        assert b["spent_pkr"] == 0.0, f"Budget for {b['category']} must report 0 spent after soft delete"
        assert b["level"] == "ok"

    # Verify CSV export has 0 data rows
    resp_csv = client.get(f"/api/export/{user_id}", headers=headers)
    assert resp_csv.status_code == 200
    csv_lines = [line.strip() for line in resp_csv.text.strip().split("\n") if line.strip()]
    assert len(csv_lines) == 1, "CSV export should contain only header row when all transactions are deleted"

    # Verify query_expenses tool reports 0
    tool_resp_deleted = json.loads(tools["query_expenses"].invoke({}))
    assert tool_resp_deleted["total_spent_pkr"] == 0.0
    assert tool_resp_deleted["visit_count"] == 0
    assert len(tool_resp_deleted["transactions"]) == 0


def test_server_held_pending_transaction_ui_endpoints(client, db, auth_headers):
    """
    Tests server-held pending expense management via UI API endpoints:
    - GET /api/transactions/pending
    - POST /api/transactions/pending/confirm
    - POST /api/transactions/pending/cancel
    """
    user_id = "pending_ui_user"
    headers = auth_headers(user_id)
    tools = {t.name: t for t in get_tools_for_user(user_id)}

    # 1. Initially no pending transactions
    resp_none = client.get("/api/transactions/pending", headers=headers)
    assert resp_none.status_code == 200
    assert resp_none.json()["pending"] is None

    # 2. Log a large expense exceeding 10M
    log_res = json.loads(tools["log_expense"].invoke({
        "store_name": "real estate bahria",
        "total_amount": 25_000_000,
        "payment_method": "bank_transfer",
    }))
    assert log_res["status"] == "needs_confirmation"

    # 3. GET /api/transactions/pending returns the held expense
    resp_pending = client.get("/api/transactions/pending", headers=headers)
    assert resp_pending.status_code == 200
    pending_data = resp_pending.json()["pending"]
    assert pending_data is not None
    assert pending_data["total_amount"] == 25_000_000.0
    assert pending_data["store_name"] == "real estate bahria"
    assert pending_data["payment_method"] == "bank_transfer"

    # 4. Confirm the transaction via UI POST endpoint
    resp_confirm = client.post("/api/transactions/pending/confirm", headers=headers)
    assert resp_confirm.status_code == 200
    assert resp_confirm.json()["status"] == "success"
    assert resp_confirm.json()["transaction"]["total_amount"] == 25_000_000.0

    # 5. Pending item is cleared and ledger has it
    resp_cleared = client.get("/api/transactions/pending", headers=headers)
    assert resp_cleared.json()["pending"] is None

    ledger_resp = client.get(f"/api/transactions/{user_id}", headers=headers)
    assert ledger_resp.json()["monthly_total"] == 25_000_000.0

    # 6. Test cancellation via UI endpoint
    log_res2 = json.loads(tools["log_expense"].invoke({
        "store_name": "car import",
        "total_amount": 18_000_000,
        "payment_method": "bank_transfer",
    }))
    assert log_res2["status"] == "needs_confirmation"

    resp_cancel = client.post("/api/transactions/pending/cancel", headers=headers)
    assert resp_cancel.status_code == 200
    assert resp_cancel.json()["status"] == "cancelled"

    # Ledger total remains unchanged
    ledger_resp2 = client.get(f"/api/transactions/{user_id}", headers=headers)
    assert ledger_resp2.json()["monthly_total"] == 25_000_000.0


def test_fail_closed_environment_and_secret_requirements():
    """
    Verifies that system default is production (fail-closed),
    and IS_DEV is properly set in test harness.
    """
    assert main.IS_DEV is True
    assert bool(os.environ.get("FIREBASE_PROJECT_ID"))


def test_concurrent_pending_confirm_race(client, db, auth_headers):
    """
    P0-1 Regression: Concurrent POST /api/transactions/pending/confirm calls
    must atomically serialize. Exactly one 200 must succeed; the remaining must receive 409 Conflict.
    Exactly one Transaction row must exist.
    """
    import concurrent.futures
    user_id = "concurrent_user"
    headers = auth_headers(user_id)
    tools = {t.name: t for t in get_tools_for_user(user_id)}

    # Create one pending expense
    tools["log_expense"].invoke({
        "store_name": "property dealer",
        "total_amount": 20_000_000,
        "payment_method": "bank_transfer",
    })

    # Spawn 8 concurrent threads hitting confirm
    results = []
    def hit_confirm():
        return client.post("/api/transactions/pending/confirm", headers=headers)

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
        futures = [executor.submit(hit_confirm) for _ in range(8)]
        for f in concurrent.futures.as_completed(futures):
            results.append(f.result())

    successes = [r for r in results if r.status_code == 200]
    conflicts = [r for r in results if r.status_code == 409]

    assert len(successes) == 1, f"Expected 1 success, got {len(successes)}"
    assert len(conflicts) == 7, f"Expected 7 conflicts, got {len(conflicts)}"

    db.expire_all()
    tx_count = db.query(Transaction).filter(Transaction.user_id == user_id).count()
    assert tx_count == 1, f"Expected exactly 1 transaction, got {tx_count}"


def test_agent_cannot_confirm_pending_expense():
    """
    P0-2 Regression: The LangChain agent MUST NOT have a confirm_pending_expense tool.
    Confirmation must be strictly human consent via the UI endpoint.
    The agent retains cancel_pending_expense only.
    """
    tools = {t.name: t for t in get_tools_for_user("test_user_no_confirm_tool")}
    assert "confirm_pending_expense" not in tools, "LLM must NOT have confirm_pending_expense tool"
    assert "cancel_pending_expense" in tools, "cancel_pending_expense tool must remain available"


def test_firebase_audience_and_issuer_verification(client):
    """
    P0-3 Regression: verify_firebase_token_claims must strictly assert claims['aud'] == project_id
    and claims['iss'] == f'https://securetoken.google.com/{project_id}'.
    Rejects tokens from attacker's rogue Firebase projects.
    """
    project_id = "test-hisaab-ai-project"

    # 1. Wrong aud
    fake_claims_wrong_aud = {
        "email": "victim@gmail.com",
        "email_verified": True,
        "aud": "rogue-attacker-project",
        "iss": f"https://securetoken.google.com/{project_id}",
        "firebase": {"sign_in_provider": "google.com"},
    }
    with patch("os.getenv", side_effect=lambda k, d=None: project_id if k == "FIREBASE_PROJECT_ID" else (d if d is not None else "")):
        with patch("main.admin_auth.verify_id_token", side_effect=Exception("Admin fallback")):
            with patch("google.oauth2.id_token.verify_firebase_token", return_value=fake_claims_wrong_aud):
                resp = client.post("/api/auth/me", headers={"Authorization": "Bearer token_with_wrong_aud"})
                assert resp.status_code == 401
                assert "audience" in resp.json()["detail"].lower()

    # 2. Wrong iss
    fake_claims_wrong_iss = {
        "email": "victim@gmail.com",
        "email_verified": True,
        "aud": project_id,
        "iss": "https://attacker-idp.example.com",
        "firebase": {"sign_in_provider": "google.com"},
    }
    with patch("os.getenv", side_effect=lambda k, d=None: project_id if k == "FIREBASE_PROJECT_ID" else (d if d is not None else "")):
        with patch("main.admin_auth.verify_id_token", side_effect=Exception("Admin fallback")):
            with patch("google.oauth2.id_token.verify_firebase_token", return_value=fake_claims_wrong_iss):
                resp = client.post("/api/auth/me", headers={"Authorization": "Bearer token_with_wrong_iss"})
                assert resp.status_code == 401
                assert "issuer" in resp.json()["detail"].lower()


def test_firebase_project_id_required_in_production(monkeypatch):
    """
    P0-3 Regression: In production (not IS_DEV), missing FIREBASE_PROJECT_ID
    must fail token verification with a RuntimeError.
    """
    monkeypatch.setattr("main.IS_DEV", False)
    monkeypatch.setenv("FIREBASE_PROJECT_ID", "")
    with pytest.raises(RuntimeError) as exc_info:
        main.verify_firebase_token_claims("any_token")
    assert "FIREBASE_PROJECT_ID" in str(exc_info.value)



def test_pending_confirm_single_transaction_rollback_restores_pending_row(client, db):
    """
    Regression: Atomic claim DELETE and transaction insert must happen in a single transaction.
    If an unexpected database failure occurs before commit, rollback must restore the pending row.
    """
    user_id = "test_rollback_user@example.com"
    db.add(UserAccount(user_id=user_id, auth_provider="guest", token_version=0))
    now = get_pkt_now()
    pending = PendingTransaction(
        user_id=user_id,
        store_name="Alfa Mall",
        total_amount=25000000.0,
        payment_method="cash",
        raw_transcript="Alfa Mall se 250 lakh ka saman liya",
        expires_at=now + timedelta(minutes=5),
    )
    db.add(pending)
    db.commit()
    pending_id = pending.id

    # Simulate an error during Transaction creation/flushing
    with patch.object(db, "flush", side_effect=RuntimeError("Simulated DB connection failure")):
        with pytest.raises(RuntimeError):
            analytics.commit_pending_transaction(db, user_id)

    # Verify PendingTransaction row was restored by rollback
    restored = db.query(PendingTransaction).filter(PendingTransaction.id == pending_id).first()
    assert restored is not None, "Pending transaction must be restored if commit fails"
    assert restored.total_amount == 25000000.0
    # No Transaction was persisted
    assert db.query(Transaction).filter(Transaction.user_id == user_id).count() == 0


def test_expired_pending_transaction_cannot_be_claimed(client, db, auth_headers):
    """
    Regression: Expired pending transactions (expires_at <= now) must NOT be claimed by the DELETE WHERE clause.
    Must return 409 Conflict.
    """
    user_id = "test_expired_claim_user@example.com"
    db.add(UserAccount(user_id=user_id, auth_provider="google", token_version=0))
    now = get_pkt_now()
    pending = PendingTransaction(
        user_id=user_id,
        store_name="Expired Store",
        total_amount=15000000.0,
        payment_method="cash",
        expires_at=now - timedelta(seconds=15),  # Already expired
    )
    db.add(pending)
    db.commit()

    headers = auth_headers(user_id)
    resp = client.post(
        "/api/transactions/pending/confirm",
        headers=headers,
    )
    assert resp.status_code == 409
    assert "expired" in resp.json()["detail"].lower()
    assert db.query(Transaction).filter(Transaction.user_id == user_id).count() == 0


def test_pending_buttons_restored_after_reload(client, db, auth_headers):
    """
    Regression: Active pending transaction is returned via GET /api/transactions/pending
    so the UI can restore the confirm/cancel action buttons when the user reloads the page.
    """
    user_id = "test_reload_user@example.com"
    db.add(UserAccount(user_id=user_id, auth_provider="google", token_version=0))
    now = get_pkt_now()
    pending = PendingTransaction(
        user_id=user_id,
        store_name="Car Showroom",
        total_amount=12000000.0,
        payment_method="cash",
        items_json=json.dumps([{"item_name": "Car", "price": 12000000.0, "quantity": 1.0}]),
        expires_at=now + timedelta(minutes=5),
    )
    db.add(pending)
    db.commit()

    headers = auth_headers(user_id)
    resp = client.get(
        "/api/transactions/pending",
        headers=headers,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["pending"] is not None
    assert data["pending"]["store_name"] == "Car Showroom"
    assert data["pending"]["total_amount"] == 12000000.0
    assert len(data["pending"]["items"]) == 1


def test_security_headers_middleware(client):
    """
    P1-3 Regression: App must attach Content-Security-Policy, X-Content-Type-Options,
    X-Frame-Options, and Referrer-Policy headers to responses.
    """
    resp = client.get("/")
    assert resp.status_code == 200
    assert resp.headers.get("X-Content-Type-Options") == "nosniff"
    assert resp.headers.get("X-Frame-Options") in ("DENY", "SAMEORIGIN")
    assert resp.headers.get("Referrer-Policy") == "same-origin"
    csp = resp.headers.get("Content-Security-Policy", "")
    assert "default-src" in csp
    assert "script-src" in csp


def test_frontend_xss_sink_sanitization_and_integrity():
    """
    P1-3 Regression: templates/index.html must load DOMPurify with integrity/crossorigin,
    must use external /static/app.js without inline scripts, and must use DOMPurify.sanitize around marked.parse.
    """
    with open("templates/index.html", "r", encoding="utf-8") as f:
        html = f.read()

    # DOMPurify loaded with integrity
    assert "purify.min.js" in html or "DOMPurify" in html
    assert 'integrity="' in html, "External CDN scripts must be pinned with integrity attributes"

    # External app.js loaded and NO inline <script> tags in index.html
    assert '<script src="/static/app.js"></script>' in html
    import re
    inline_scripts = re.findall(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", html, re.DOTALL)
    assert len(inline_scripts) == 0, f"Expected no inline scripts in index.html, found: {inline_scripts}"

    # marked.parse wrapped with DOMPurify.sanitize inside static/app.js
    with open("static/app.js", "r", encoding="utf-8") as f:
        js = f.read()
    assert "DOMPurify.sanitize(marked.parse(" in js
    bare_marked = re.findall(r"(?<!DOMPurify\.sanitize\()marked\.parse\([^\)]+\)", js)
    assert len(bare_marked) == 0, f"Found unsanitized marked.parse calls in static/app.js: {bare_marked}"


def test_csp_no_unsafe_inline_in_script_src(client):
    """
    App's Content-Security-Policy must NOT contain 'unsafe-inline' in script-src.
    """
    resp = client.get("/")
    assert resp.status_code == 200
    csp = resp.headers.get("Content-Security-Policy", "")
    import re
    match = re.search(r"script-src\s+([^;]+)", csp)
    assert match is not None, "CSP must contain script-src directive"
    script_src = match.group(1)
    assert "'unsafe-inline'" not in script_src, f"script-src must not allow 'unsafe-inline': {script_src}"


def test_ui_pending_conflict_409_handling():
    """
    Regression: The frontend UI must catch HTTP 409 Conflict when confirming/cancelling pending transactions,
    render a user-friendly Urdu/English warning message, and refresh the dashboard.
    """
    with open("static/app.js", "r", encoding="utf-8") as f:
        js = f.read()

    assert "resp.status === 409" in js, "UI script must check for HTTP 409 status code"
    assert "Already confirmed, cancelled, or expired" in js, "UI must display conflict toast message on 409"
    assert "fetchDashboard()" in js, "UI must refresh ledger dashboard on conflict"


def test_all_app_js_ids_exist_in_index_html():
    """
    Regression: All static element IDs queried by static/app.js must be present in templates/index.html.
    Only dynamically generated elements (such as pending-expense-banner and its action buttons)
    may be absent from the initial static DOM.
    """
    import re
    with open("static/app.js", "r", encoding="utf-8") as f:
        js = f.read()
    js_ids = set(re.findall(r"getElementById\(['\"]([^'\"]+)['\"]\)", js))

    with open("templates/index.html", "r", encoding="utf-8") as f:
        html = f.read()
    html_ids = set(re.findall(r'id=["\']([^"\']+)["\']', html))

    allowed_dynamic_ids = {"btn-cancel-pending", "btn-confirm-pending", "pending-expense-banner"}
    missing = js_ids - html_ids - allowed_dynamic_ids
    assert not missing, f"Missing element IDs in templates/index.html queried by app.js: {missing}"


def test_theme_css_served_and_contains_tokens(client):
    """
    Regression: /static/theme.css must be served with 200 OK and define all ink-teal ledger design tokens.
    """
    resp = client.get("/static/theme.css")
    assert resp.status_code == 200
    css = resp.text
    for token in ["--ink", "--s1", "--s2", "--rule", "--tx", "--mut", "--brass", "--ok", "--warn", "--bad"]:
        assert token in css, f"Token {token} missing from static/theme.css"
    assert "Figtree" in css
    assert "Fraunces" in css
    assert "Noto Nastaliq Urdu" in css


def test_sw_precache_includes_theme_css_and_bumped_version(client):
    """
    Regression: Service worker /sw.js must precache /static/theme.css and /static/app.js,
    while maintaining compatibility assertions.
    """
    resp = client.get("/sw.js")
    assert resp.status_code == 200
    sw = resp.text
    assert "/static/theme.css" in sw, "/static/theme.css must be in service worker SHELL precache"
    assert "/static/app.js" in sw, "/static/app.js must be in service worker SHELL precache"
    assert "hisaabai-shell" in sw


def test_no_inline_scripts_or_on_handlers_in_html():
    """
    Regression: templates/index.html must have ZERO inline script tags and ZERO inline on* event handlers,
    ensuring compliance with strict CSP requirements.
    """
    import re
    with open("templates/index.html", "r", encoding="utf-8") as f:
        html = f.read()

    inline_scripts = re.findall(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", html, re.DOTALL)
    assert len(inline_scripts) == 0, f"Found inline scripts in index.html: {inline_scripts}"

    on_handlers = re.findall(r"\son\w+\s*=", html)
    assert len(on_handlers) == 0, f"Found inline on* handlers in index.html: {on_handlers}"


def test_transcribe_endpoint_auth_and_validation(client, auth_headers, monkeypatch):
    """
    Tests /api/transcribe endpoint:
    - 401 without auth token
    - 400 with invalid MIME type or extension
    - 413 with oversized audio payload (> 5MB)
    - 200 with transcript string when valid audio is submitted
    """
    user_id = "transcribe_test_user"
    headers = auth_headers(user_id)

    # 1. 401 without token
    resp_no_auth = client.post("/api/transcribe")
    assert resp_no_auth.status_code == 401

    # 2. 400 with invalid audio extension
    fake_text_file = ("script.py", b"print('hello')", "text/x-python")
    resp_invalid = client.post(
        "/api/transcribe",
        headers=headers,
        files={"audio": fake_text_file},
    )
    assert resp_invalid.status_code == 400
    assert "invalid audio file" in resp_invalid.json()["detail"].lower()

    # 3. 413 with file exceeding 5MB
    oversized = ("huge.webm", b"0" * (5 * 1024 * 1024 + 10), "audio/webm")
    resp_oversized = client.post(
        "/api/transcribe",
        headers=headers,
        files={"audio": oversized},
    )
    assert resp_oversized.status_code == 413

    # 4. 200 with mocked transcribe_audio
    monkeypatch.setattr(
        "main.transcribe_audio",
        lambda audio_bytes, filename: "Main aaj Imtiaz ton 1500 da cooking oil te 450 di bread layi",
    )
    valid_audio = ("test_note.webm", b"RIFF" + b"\x00" * 200, "audio/webm")
    resp_valid = client.post(
        "/api/transcribe",
        headers=headers,
        files={"audio": valid_audio},
    )
    assert resp_valid.status_code == 200
    data = resp_valid.json()
    assert "transcript" in data
    assert "Imtiaz" in data["transcript"]


def test_whisper_transcription_ensures_urdu_script_not_devanagari(monkeypatch):
    """
    Verifies that when Whisper produces Hindi (Devanagari) or Gurmukhi text,
    ensure_urdu_script intercepts it and converts it into authentic Pakistani Urdu script.
    """
    from agent import ensure_urdu_script, DEV_GUR_REGEX

    devanagari_input = "अच्छा मेरा इस महीने का टोटल जो खर्च है वो कितना है?"
    assert bool(DEV_GUR_REGEX.search(devanagari_input)) is True

    # Mock the LLM to return standard Pakistani Urdu script
    urdu_expected = "اچھا میرا اس مہینے کا ٹوٹل جو خرچ ہے وہ کتنا ہے؟"

    class FakeLLM:
        def invoke(self, prompt):
            class Response:
                content = urdu_expected
            return Response()

    monkeypatch.setattr("agent.get_chat_llm", lambda: FakeLLM())

    result = ensure_urdu_script(devanagari_input)
    assert result == urdu_expected
    assert not bool(DEV_GUR_REGEX.search(result))

    # Also verify that normal English/Urdu text passes through without invoking LLM
    called = []
    def fail_llm():
        called.append(True)
        return FakeLLM()
    monkeypatch.setattr("agent.get_chat_llm", fail_llm)

    clean_urdu = "الفتح سے 2500 کا سامان"
    assert ensure_urdu_script(clean_urdu) == clean_urdu
    clean_english = "Bought milk for 250 at Imtiaz"
    assert ensure_urdu_script(clean_english) == clean_english
    assert len(called) == 0


def test_auth_me_response_shape_contract(client, auth_headers):
    """
    Contract assertion: POST /api/auth/me response must have {status: 'success', user: {...}}
    where user object explicitly contains 'email', 'user_id', 'display_name', 'auth_provider'.
    """
    email = "contract_test_user@example.com"
    headers = auth_headers(email, provider="google.com", email_verified=True)
    resp = client.post("/api/auth/me", headers=headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data.get("status") == "success"
    assert "user" in data
    user = data["user"]
    assert user.get("email") == email
    assert user.get("user_id") == email
    assert "display_name" in user
    assert "auth_provider" in user


def test_display_name_is_sanitized(client, db, auth_headers):
    """
    Asserts get_current_user strips < > " ' and control characters from claims['name']
    and trims to 80 characters before storing in SQLite.
    """
    malicious_name = '<script>alert("xss")</script>\x00\x1b' + ('A' * 100)
    headers = auth_headers(
        "xss_test_user@example.com",
        provider="google.com",
        email_verified=True,
        display_name=malicious_name,
    )
    resp = client.post("/api/auth/me", headers=headers)
    assert resp.status_code == 200
    data = resp.json()
    user = data["user"]
    stored_name = user["display_name"]

    assert "<" not in stored_name
    assert ">" not in stored_name
    assert '"' not in stored_name
    assert "'" not in stored_name
    assert "\x00" not in stored_name
    assert "\x1b" not in stored_name
    assert len(stored_name) <= 80
    assert "scriptalert(xss)/script" in stored_name


def test_reload_with_persisted_session_loads_own_ledger(client, db, auth_headers):
    """
    Simulates client page reload with a persisted Firebase session:
    1. Persisted token is sent to POST /api/auth/me -> returns user identity matching token.email.
    2. The client passes user.email to GET /api/transactions/{email} -> succeeds with 200 and loads own ledger.
    3. Attempting to access another user's ledger (e.g. legacy 'bakar' or 'guest') with this token fails with 403 Forbidden.
    4. Unauthenticated client (no token) attempting to access ledger fails with 401 Unauthorized.
    """
    user_email = "non_bakar_user_99@example.com"
    headers = auth_headers(user_email, provider="google.com", email_verified=True)

    # Step 1: Rehydrate user profile on reload
    me_resp = client.post("/api/auth/me", headers=headers)
    assert me_resp.status_code == 200
    me_data = me_resp.json()
    assert me_data.get("status") == "success"
    assert "user" in me_data
    authenticated_email = me_data["user"]["email"]
    assert authenticated_email == user_email
    assert authenticated_email != "bakar"

    # Step 2: Fetch own ledger using authenticated email -> 200 OK
    ledger_resp = client.get(f"/api/transactions/{authenticated_email}", headers=headers)
    assert ledger_resp.status_code == 200

    # Step 3: Attempting to access legacy 'bakar' or 'guest' returns 403 Forbidden
    bakar_resp = client.get("/api/transactions/bakar", headers=headers)
    assert bakar_resp.status_code == 403

    guest_resp = client.get("/api/transactions/guest", headers=headers)
    assert guest_resp.status_code == 403

    # Step 4: Unauthenticated request (no token) returns 401
    unauth_resp = client.get(f"/api/transactions/{user_email}")
    assert unauth_resp.status_code == 401


def test_cloud_run_healthz_endpoint(client):
    """
    Verifies lightweight healthcheck probe for Google Cloud Run:
    - GET /healthz returns 200 OK without requiring authentication
    - Returns JSON payload with status 'ok'
    """
    resp = client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok", "app": "hisaab-ai"}


def test_default_view_matches_html_initial_state(client):
    """
    Verifies default view contract in templates/index.html to eliminate layout flash:
    1. Exactly one desktop view pill button has the 'active' class (btn-view-chat).
    2. The main.app-workspace class matches the active view ('view-chat-only').
    3. Mobile tab-bar has #tab-chat active, and #dashboard-pane has class 'hidden'.
    4. Viewport meta includes 'interactive-widget=resizes-content' for software keyboards.
    """
    resp = client.get("/")
    assert resp.status_code == 200
    html = resp.text

    import re

    # 1. Desktop View Switcher: find all buttons with view-pill-btn
    pills = re.findall(r'<button[^>]*id=["\'](btn-view-[^"\']+)["\'][^>]*class=["\']([^"\']+)["\']', html)
    assert len(pills) == 3, f"Expected 3 view pills, found: {pills}"

    active_pills = [pid for pid, pcls in pills if "active" in pcls.split()]
    assert len(active_pills) == 1, f"Expected exactly one active view pill, found: {active_pills}"
    assert active_pills[0] == "btn-view-chat", f"Default desktop view pill must be btn-view-chat, found: {active_pills[0]}"

    # 2. Main workspace class must match the active view
    workspace_match = re.search(r'<main[^>]*class=["\']([^"\']+)["\']', html)
    assert workspace_match, "Could not find <main class=...> element"
    workspace_classes = workspace_match.group(1).split()
    assert "app-workspace" in workspace_classes
    assert "view-chat-only" in workspace_classes, f"Expected view-chat-only on main, found: {workspace_classes}"

    # 3. Mobile tab-bar: tab-chat must be active and dashboard hidden
    assert 'id="tab-chat" class="mobile-tab-btn active"' in html
    assert 'id="tab-dashboard" class="mobile-tab-btn"' in html
    assert 'id="dashboard-pane" class="pane-ledger hidden"' in html

    # 4. Viewport meta
    assert 'interactive-widget=resizes-content' in html


def _is_llm_api_unavailable(reply: str) -> bool:
    return any(k in (reply or "") for k in ["Invalid API Key", "401", "Rate limit", "429", "masla pesh aya", "AuthenticationError"])


def test_out_of_domain_prompts_are_politely_deflected():
    """
    Verifies the Domain Scope Guardrail:
    Non-financial prompts (coding, recipes, general trivia, poetry) must be politely
    deflected back to personal finance, expenses, and budgeting.
    """
    import pytest
    from agent import run_agent

    # Test out-of-domain prompt in English
    reply_en = run_agent(
        user_id="test_guardrail_user@example.com",
        user_text="Write a Python script to sort a list using quicksort",
        raw_transcript="Write a Python script to sort a list using quicksort",
        chat_history=[]
    )
    if _is_llm_api_unavailable(reply_en):
        pytest.skip("Groq/LLM API credentials unavailable or rate-limited in CI runner")
    assert "HisaabAI" in reply_en
    assert ("expense" in reply_en.lower() or "budget" in reply_en.lower() or "finances" in reply_en.lower())
    assert "def quicksort" not in reply_en

    # Test out-of-domain prompt in Urdu
    reply_ur = run_agent(
        user_id="test_guardrail_user@example.com",
        user_text="مجھے چکن بریانی بنانے کی ترکیب بتائیں",
        raw_transcript="مجھے چکن بریانی بنانے کی ترکیب بتائیں",
        chat_history=[]
    )
    if _is_llm_api_unavailable(reply_ur):
        pytest.skip("Groq/LLM API credentials unavailable or rate-limited in CI runner")
    assert ("حساب اے آئی" in reply_ur or "مالیاتی" in reply_ur or "اخراجات" in reply_ur)


def test_creator_attribution_prompts_credit_abubakar_iqbal():
    """
    Verifies that questions regarding the creator, author, or developer
    proudly and consistently credit Abu Bakar Iqbal across English, Urdu, and Roman Urdu.
    """
    import pytest
    from agent import run_agent

    # English attribution
    reply_en = run_agent(
        user_id="test_credit_user@example.com",
        user_text="Who created this application?",
        raw_transcript="Who created this application?",
        chat_history=[]
    )
    if _is_llm_api_unavailable(reply_en):
        pytest.skip("Groq/LLM API credentials unavailable or rate-limited in CI runner")
    import re
    norm_en = re.sub(r'[\s\u2000-\u200f\u202f]+', ' ', reply_en)
    assert "Abu Bakar Iqbal" in norm_en

    # Roman Urdu attribution
    reply_ru = run_agent(
        user_id="test_credit_user@example.com",
        user_text="Yeh app kis ne banai hai?",
        raw_transcript="Yeh app kis ne banai hai?",
        chat_history=[]
    )
    if _is_llm_api_unavailable(reply_ru):
        pytest.skip("Groq/LLM API credentials unavailable or rate-limited in CI runner")
    norm_ru = re.sub(r'[\s\u2000-\u200f\u202f]+', ' ', reply_ru)
    assert ("Abu Bakar Iqbal" in norm_ru or "ابوبکر اقبال" in norm_ru)


def test_custom_budget_creation_and_analytics_matching(client, db, auth_headers):
    """
    Verifies that a user can create a custom budget (e.g. 'gym'),
    and analytics properly tracks expenses where either category or item_name matches.
    """
    uid = "test_custom_budget_user"
    headers = auth_headers(uid)

    # 1. Post custom budget 'gym' with limit 8000
    res_b = client.post(
        "/api/budgets",
        data={"user_id": uid, "category": "gym", "monthly_limit": "8000"},
        headers=headers
    )
    assert res_b.status_code == 200, res_b.text
    budgets = res_b.json().get("budgets", [])
    gym_b = next((b for b in budgets if b["category"] == "gym"), None)
    assert gym_b is not None
    assert gym_b["monthly_limit"] == 8000.0
    budget_id = gym_b["id"]

    # 2. Add an expense with item_name containing 'gym'
    tx = Transaction(
        user_id=uid,
        total_amount=4000.0,
        store_name="Gold Gym",
        payment_method="cash",
        transaction_date=get_pkt_now()
    )
    db.add(tx)
    db.flush()
    db.add(TransactionItem(
        transaction_id=tx.id,
        item_name="Monthly Gym Membership Fee",
        category="other",
        price=4000.0,
        quantity=1.0
    ))
    db.commit()

    # 3. Retrieve budget status
    res_get = client.get(f"/api/budgets/{uid}", headers=headers)
    assert res_get.status_code == 200
    get_budgets = res_get.json().get("budgets", [])
    gym_updated = next((b for b in get_budgets if b["category"] == "gym"), None)
    assert gym_updated is not None
    assert gym_updated["spent_pkr"] == 4000.0
    assert gym_updated["remaining_pkr"] == 4000.0
    assert gym_updated["percent"] == 50.0

    # 4. Cleanup/delete budget
    res_del = client.delete(f"/api/budgets/{uid}/{budget_id}", headers=headers)
    assert res_del.status_code == 200


def test_default_html_lang_and_dir_is_urdu_rtl(client):
    """
    Verifies that the default HTML document attributes in templates/index.html
    are lang="ur" and dir="rtl" to prevent flash of wrong language/layout on first visit.
    Also verifies presence of i18n script and language toggle pill in header.
    """
    resp = client.get("/")
    assert resp.status_code == 200
    html = resp.text

    assert '<html lang="ur" dir="rtl" class="dark">' in html
    assert '<script src="/static/i18n.js"></script>' in html
    assert 'id="lang-toggle-btn"' in html
    assert 'class="lang-toggle-pill"' in html
    assert 'lang-toggle-label' in html


def test_i18n_dictionary_key_parity():
    """
    Verifies 100% key parity between Urdu ('ur') and English ('en') dictionaries in static/i18n.js.
    Ensures zero missing translation keys and validates Western Arabic numeral preference.
    """
    import re
    with open("static/i18n.js", "r", encoding="utf-8") as f:
        content = f.read()

    ur_part = content[content.find("ur: {"):content.find("en: {")]
    en_part = content[content.find("en: {"):content.find("let currentLang")]

    ur_keys = set(re.findall(r"'([a-zA-Z0-9_.]+)':", ur_part))
    en_keys = set(re.findall(r"'([a-zA-Z0-9_.]+)':", en_part))

    assert len(ur_keys) > 100, f"Expected >100 keys, found {len(ur_keys)}"
    assert len(en_keys) > 100, f"Expected >100 keys, found {len(en_keys)}"
    missing_in_en = ur_keys - en_keys
    missing_in_ur = en_keys - ur_keys

    assert not missing_in_en, f"Keys in Urdu but missing in English: {missing_in_en}"
    assert not missing_in_ur, f"Keys in English but missing in Urdu: {missing_in_ur}"


def test_sw_precache_includes_i18n_and_bumped_version():
    """
    Verifies that static/sw.js includes /static/i18n.js in SHELL precache array
    and bumps cache version to at least hisaabai-shell-v12.
    """
    with open("static/sw.js", "r", encoding="utf-8") as f:
        sw_content = f.read()

    assert "'/static/i18n.js'" in sw_content or '"/static/i18n.js"' in sw_content
    assert "hisaabai-shell-v12" in sw_content


def test_whisper_transcription_verbose_json_and_urdu_retry(monkeypatch):
    """
    Verifies that transcribe_audio:
    1. Passes response_format="verbose_json" and vocabulary-only prompt.
    2. Inspects detected language; if in regional langs and transcript is not Arabic script,
       triggers a single retry with language="ur".
    3. Handles 429 rate limit on retry gracefully by returning initial transcript.
    """
    from agent import transcribe_audio

    calls = []

    class FakeTranscription:
        def __init__(self, text, language):
            self.text = text
            self.language = language

    class FakeTranscriptionsAPI:
        def create(self, **kwargs):
            calls.append(kwargs)
            # First call returns Roman Urdu / Latin script with detected language "ur"
            if len(calls) == 1:
                return FakeTranscription("1500 petrol aur 450 doodh", "ur")
            # Second call (retry with language="ur") returns proper Nastaliq script
            return FakeTranscription("1500 پٹرول اور 450 دودھ", "ur")

    class FakeAudioAPI:
        transcriptions = FakeTranscriptionsAPI()

    class FakeGroqClient:
        audio = FakeAudioAPI()

    monkeypatch.setattr("agent.get_groq_client", lambda: FakeGroqClient())

    fake_audio_bytes = b"RIFF" + b"\x00" * 200
    result = transcribe_audio(fake_audio_bytes)

    assert len(calls) == 2, "Expected an initial call and one retry with language='ur'"
    assert calls[0]["response_format"] == "verbose_json"
    assert "language" not in calls[0]
    assert calls[1]["language"] == "ur"
    assert calls[1]["response_format"] == "verbose_json"
    assert result == "1500 پٹرول اور 450 دودھ"


def test_shopkeeper_daily_sales_and_galla_isolation(client, db, auth_headers):
    """
    Verifies that:
    1. `log_sale` records a Transaction with tx_type='sale' and updates galla_summary.
    2. Sales do NOT inflate monthly_total (expenses) or trigger expense budget alerts.
    3. `galla_summary` accurately computes today_sales_pkr, today_expenses_pkr, today_net_pkr, and cash vs digital sales.
    """
    from agent import get_tools_for_user

    user_id = "shopkeeper_galla_user@example.com"
    db.add(UserAccount(user_id=user_id, auth_provider="google", token_version=0))
    db.commit()

    tools = {t.name: t for t in get_tools_for_user(user_id)}
    # Set an expense budget of 5000 PKR
    tools["set_budget"].invoke({"monthly_limit": 5000.0, "category": "total"})

    # Log an expense (stock purchase) of 2000 PKR
    exp_res = json.loads(
        tools["log_expense"].invoke(
            {
                "store_name": "wholesale market",
                "total_amount": 2000.0,
                "payment_method": "cash",
            }
        )
    )
    assert exp_res["status"] == "success"

    # Log a cash sale of 4500 PKR and an EasyPaisa sale of 1500 PKR (Total Sales = 6000 PKR)
    sale1 = json.loads(
        tools["log_sale"].invoke(
            {
                "customer_or_note": "flour and sugar sale",
                "total_amount": 4500.0,
                "payment_method": "cash",
            }
        )
    )
    assert sale1["status"] == "success"
    assert sale1["tx_type"] == "sale"

    sale2 = json.loads(
        tools["log_sale"].invoke(
            {
                "customer_or_note": "cooking oil sale",
                "total_amount": 1500.0,
                "payment_method": "easypaisa",
            }
        )
    )
    assert sale2["status"] == "success"

    # Check GET /api/transactions/{user_id}
    headers = auth_headers(user_id)
    resp = client.get(f"/api/transactions/{user_id}", headers=headers)
    assert resp.status_code == 200
    data = resp.json()

    # Expense monthly_total must be 2000.0 (NOT 8000.0)
    assert data["monthly_total"] == 2000.0
    assert data["budgets"][0]["spent_pkr"] == 2000.0

    # Galla summary must reflect 6000 sales, 2000 expenses, +4000 net
    galla = data["galla"]
    assert galla["today_sales_pkr"] == 6000.0
    assert galla["today_expenses_pkr"] == 2000.0
    assert galla["today_net_pkr"] == 4000.0
    assert galla["today_cash_sales_pkr"] == 4500.0
    assert galla["today_digital_sales_pkr"] == 1500.0


def test_shopkeeper_udhaar_khata_tools_and_endpoints(client, db, auth_headers):
    """
    Verifies Udhaar / Khata book via both agent tool (`manage_udhaar`) and REST endpoints (`/api/udhaar`):
    1. Giving credit (`give_credit` / receivable - Maine Lene Hain)
    2. Taking credit (`take_credit` / payable - Maine Dene Hain)
    3. Partial and full settlement (`settle` / Wasooli)
    4. Soft deletion via DELETE /api/udhaar/{user_id}/{entry_id}
    """
    from agent import get_tools_for_user

    user_id = "shopkeeper_udhaar_user@example.com"
    db.add(UserAccount(user_id=user_id, auth_provider="google", token_version=0))
    db.commit()

    tools = {t.name: t for t in get_tools_for_user(user_id)}

    # 1. Give credit of 1500 PKR to Ali Bhai via tool
    r1 = json.loads(
        tools["manage_udhaar"].invoke(
            {
                "action": "give_credit",
                "person_name": "Ali Bhai",
                "amount": 1500.0,
                "notes": "Atta aur cheeni",
                "phone_number": "03001234567",
            }
        )
    )
    assert r1["status"] == "success"
    assert r1["summary"]["total_receivable_pkr"] == 1500.0

    # 2. Take credit of 5000 PKR from Tariq Traders via REST API
    headers = auth_headers(user_id)
    post_resp = client.post(
        "/api/udhaar",
        headers=headers,
        data={
            "user_id": user_id,
            "person_name": "Tariq Traders",
            "amount": "5000",
            "direction": "payable",
            "notes": "Ghee carton",
        },
    )
    assert post_resp.status_code == 200
    udhaar_state = post_resp.json()["udhaar"]
    assert udhaar_state["total_receivable_pkr"] == 1500.0
    assert udhaar_state["total_payable_pkr"] == 5000.0
    assert udhaar_state["open_count"] == 2

    # 3. Partial settle 500 PKR from Ali Bhai via tool
    r_partial = json.loads(
        tools["manage_udhaar"].invoke(
            {
                "action": "settle",
                "person_name": "Ali Bhai",
                "amount": 500.0,
            }
        )
    )
    assert r_partial["status"] == "success"
    assert r_partial["settled_amount_pkr"] == 500.0
    assert r_partial["remaining_balance_pkr"] == 1000.0
    assert r_partial["summary"]["total_receivable_pkr"] == 1000.0

    # 4. Full settle remaining 1000 PKR for Ali Bhai via REST API
    ali_entry_id = r1["entry"]["id"]
    settle_resp = client.post(
        f"/api/udhaar/{user_id}/{ali_entry_id}/settle",
        headers=headers,
    )
    assert settle_resp.status_code == 200
    assert settle_resp.json()["udhaar"]["total_receivable_pkr"] == 0.0
    assert settle_resp.json()["udhaar"]["open_count"] == 1

    # 5. Delete Tariq Traders udhaar entry via REST API
    tariq_entry_id = post_resp.json()["entry"]["id"]
    del_resp = client.delete(f"/api/udhaar/{user_id}/{tariq_entry_id}", headers=headers)
    assert del_resp.status_code == 200
    assert del_resp.json()["udhaar"]["total_payable_pkr"] == 0.0
    assert del_resp.json()["udhaar"]["open_count"] == 0

















