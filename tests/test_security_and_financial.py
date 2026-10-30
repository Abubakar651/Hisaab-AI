import io
import json
import base64
import hashlib
import pytest
from datetime import datetime
from unittest.mock import patch, MagicMock

from database import Transaction, TransactionItem, Budget, RecurringExpense, UserAccount, get_pkt_now
from analytics import (
    validate_transaction_amount,
    check_amount_plausibility,
    category_breakdown,
    pay_recurring,
    export_rows,
    sanitize_csv_cell,
)
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


def test_forged_google_login_rejected(client):
    """Forged Google login with missing or invalid token must be rejected."""
    # 1. Missing token
    resp = client.post("/api/auth/login", json={"provider": "google", "email": "attacker@gmail.com"})
    assert resp.status_code == 400

    # 2. Forged invalid token: test real verify_firebase_id_token exception path
    with patch("google.oauth2.id_token.verify_firebase_token", side_effect=Exception("Invalid signature")):
        resp = client.post(
            "/api/auth/login",
            json={"provider": "google", "id_token": "fake_forged_jwt_token", "email": "attacker@gmail.com"},
        )
        assert resp.status_code == 401


def test_account_squatting_prevented(client, db):
    """Registering with an already existing identifier must return an error and never overwrite existing account."""
    # 1. First registration succeeds
    payload = {
        "name": "Original User",
        "email_or_phone": "victim@example.com",
        "password": "CorrectPassword123!",
    }
    resp = client.post("/api/auth/register", json=payload)
    assert resp.status_code == 200
    assert "token" in resp.json()

    # 2. Second registration for same email attempts to squat
    squatter_payload = {
        "name": "Attacker",
        "email_or_phone": "victim@example.com",
        "password": "HackerPassword999!",
    }
    resp2 = client.post("/api/auth/register", json=squatter_payload)
    assert resp2.status_code == 400
    assert "already exists" in resp2.json()["detail"].lower()

    # 3. Verify original account was not overwritten
    account = db.query(UserAccount).filter(UserAccount.user_id == "victim@example.com").first()
    assert account.display_name == "Original User"


def test_password_login_rate_limiting_and_uniform_errors(client):
    """
    6th rapid wrong-password attempt must return 429 Too Many Requests.
    Errors for unknown user and wrong password must be identical.
    """
    client_ip = "127.0.0.1"
    ident = "testuser@example.com"
    main.clear_login_rate_limit(client_ip, ident)

    # First register user
    client.post(
        "/api/auth/register",
        json={"name": "Test User", "email_or_phone": ident, "password": "ValidPassword123"},
    )

    # Unknown user attempt
    resp_unknown = client.post(
        "/api/auth/password-login",
        json={"email_or_phone": "nonexistent@example.com", "password": "SomePassword123"},
    )
    assert resp_unknown.status_code == 401
    unknown_detail = resp_unknown.json()["detail"]

    # Wrong password attempt (attempt 1)
    resp_wrong = client.post(
        "/api/auth/password-login",
        json={"email_or_phone": ident, "password": "WrongPassword!"},
    )
    assert resp_wrong.status_code == 401
    assert resp_wrong.json()["detail"] == unknown_detail, "Error messages must be uniform to prevent enumeration"

    # Rapid wrong password attempts 2, 3, 4, 5
    for _ in range(4):
        resp = client.post(
            "/api/auth/password-login",
            json={"email_or_phone": ident, "password": "WrongPassword!"},
        )
        assert resp.status_code == 401

    # 6th attempt should be rate limited with HTTP 429
    resp_6th = client.post(
        "/api/auth/password-login",
        json={"email_or_phone": ident, "password": "WrongPassword!"},
    )
    assert resp_6th.status_code == 429, f"Expected 429, got {resp_6th.status_code}"
    main.clear_login_rate_limit(client_ip, ident)


def test_password_hash_auto_migration_from_100k_to_600k(client, db):
    """Legacy 100,000-iteration PBKDF2 hashes must transparently rehash to 600,000 on login."""
    ident = "legacy@example.com"
    password = "LegacyPassword123"

    # Insert legacy hash with 100,000 iterations
    salt = b"0123456789abcdef"
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 100_000)
    legacy_hash = base64.b64encode(salt + dk).decode("utf-8")

    account = UserAccount(
        user_id=ident,
        auth_provider="credentials",
        display_name="Legacy User",
        email=ident,
        password_hash=legacy_hash,
        created_at=get_pkt_now(),
        last_login=get_pkt_now(),
    )
    db.add(account)
    db.commit()

    # Login with legacy password
    resp = client.post(
        "/api/auth/password-login",
        json={"email_or_phone": ident, "password": password},
    )
    assert resp.status_code == 200
    assert "token" in resp.json()

    # Verify that the DB hash was upgraded to 600,000 iterations
    db.refresh(account)
    assert account.password_hash.startswith("pbkdf2_sha256$600000$")


def test_amount_handling_and_confirmation(db):
    """
    Amount handling tests:
    - '200 lakh' -> 20,000,000 stays intact and requests confirmation
    - '20000 lakh' is flagged for confirmation (not rewritten to 20,000)
    - '6000 hazaar' stays 6,000,000
    - '60 lakh jazzcash' is flagged for confirmation (not truncated to 60)
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

    # 5. Test log_expense tool confirmation behavior
    tools = {t.name: t for t in get_tools_for_user("test_user_amounts")}
    log_expense_tool = tools["log_expense"]

    # Unconfirmed call for 20M returns needs_confirmation
    res_unconfirmed = json.loads(log_expense_tool.invoke({
        "store_name": "property dealer",
        "total_amount": 20_000_000,
        "payment_method": "bank_transfer",
        "confirmed": False,
    }))
    assert res_unconfirmed["status"] == "needs_confirmation"
    assert res_unconfirmed["amount"] == 20_000_000.0

    # Confirmed call with confirmed=True saves to DB
    res_confirmed = json.loads(log_expense_tool.invoke({
        "store_name": "property dealer",
        "total_amount": 20_000_000,
        "payment_method": "bank_transfer",
        "confirmed": True,
    }))
    assert res_confirmed["status"] == "success"
    assert res_confirmed["total_amount_pkr"] == 20_000_000.0


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


def test_whatsapp_namespace_isolation(client, db, auth_headers):
    """wa: ledger must be isolated from the plain phone user."""
    plain_phone = "+923001234567"
    wa_phone = "wa:+923001234567"

    # Add transaction for plain phone
    tx_plain = Transaction(
        user_id=plain_phone,
        store_name="Store Plain",
        total_amount=500.0,
        transaction_date=get_pkt_now(),
        payment_method="cash",
    )
    # Add transaction for wa: phone
    tx_wa = Transaction(
        user_id=wa_phone,
        store_name="Store WhatsApp",
        total_amount=900.0,
        transaction_date=get_pkt_now(),
        payment_method="cash",
    )
    db.add_all([tx_plain, tx_wa])
    db.commit()

    # Query plain phone ledger
    headers_plain = auth_headers(plain_phone)
    resp = client.get(f"/api/transactions/{plain_phone}", headers=headers_plain)
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


def test_guest_mode_random_uuid_and_isolation(client):
    """Guest login generates random unguessable guest ID and cannot claim arbitrary IDs."""
    resp = client.post("/api/auth/login", json={"provider": "guest"})
    assert resp.status_code == 200
    data = resp.json()
    assert "token" in data
    assert data["user"]["user_id"].startswith("guest_")
    assert len(data["user"]["user_id"]) > 10


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
