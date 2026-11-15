import pytest
from datetime import datetime, timedelta
import database
from database import Party, PartyTransaction, BusinessProfile, UdhaarEntry, get_pkt_now
import analytics


# ===========================================================================
# Tier 1: Happy Path Tests for Party & Ledger CRUD
# ===========================================================================

def test_create_customer_party_success(client, auth_headers):
    user_id = "test_party_user_1@example.com"
    headers = auth_headers(user_id)

    res = client.post(
        "/api/parties",
        json={
            "name": "Tariq Kiryana Store",
            "phone": "03001234567",
            "party_type": "customer",
            "opening_balance": 1500.0,
            "opening_balance_type": "receivable",
            "address": "Shop 12, Main Bazaar, Lahore",
            "credit_limit": 25000.0,
        },
        headers=headers,
    )
    assert res.status_code == 201
    data = res.json()
    assert data["status"] == "success"
    assert data["party"]["name"] == "Tariq Kiryana Store"
    assert data["party"]["phone"] == "03001234567"
    assert data["party"]["party_type"] == "customer"
    assert data["party"]["opening_balance"] == 1500.0
    assert data["balance"]["net_balance_pkr"] == 1500.0
    assert data["balance"]["balance_status"] == "receivable"


def test_create_supplier_party_success(client, auth_headers):
    user_id = "test_party_user_2@example.com"
    headers = auth_headers(user_id)

    res = client.post(
        "/api/parties",
        json={
            "name": "Al-Madina Flour Mills",
            "phone": "03219876543",
            "party_type": "supplier",
            "opening_balance": 50000.0,
            "opening_balance_type": "payable",
        },
        headers=headers,
    )
    assert res.status_code == 201
    data = res.json()
    assert data["party"]["party_type"] == "supplier"
    assert data["balance"]["balance_status"] == "payable"
    assert data["balance"]["net_balance_pkr"] == 50000.0


def test_get_party_detail_and_transactions(client, auth_headers):
    user_id = "test_party_user_3@example.com"
    headers = auth_headers(user_id)

    # 1. Create party
    p_res = client.post(
        "/api/parties",
        json={"name": "Bilal Ahmed", "phone": "03331122334", "opening_balance": 0.0},
        headers=headers,
    )
    assert p_res.status_code == 201
    party_id = p_res.json()["party"]["id"]

    # 2. Add credit transaction (Gave 2500 credit)
    t1_res = client.post(
        f"/api/parties/{user_id}/{party_id}/transactions",
        json={
            "amount": 2500.0,
            "tx_type": "credit",
            "description": "5 Bags Sugar",
        },
        headers=headers,
    )
    assert t1_res.status_code == 201
    assert t1_res.json()["transaction"]["amount"] == 2500.0
    assert t1_res.json()["party_summary"]["net_balance_pkr"] == 2500.0

    # 3. Add debit transaction (Received 1000 wasooli)
    t2_res = client.post(
        f"/api/parties/{user_id}/{party_id}/transactions",
        json={
            "amount": 1000.0,
            "tx_type": "debit",
            "description": "Cash wasooli payment",
        },
        headers=headers,
    )
    assert t2_res.status_code == 201
    assert t2_res.json()["party_summary"]["net_balance_pkr"] == 1500.0
    assert t2_res.json()["party_summary"]["balance_status"] == "receivable"

    # 4. Fetch party detail
    detail_res = client.get(f"/api/parties/{user_id}/{party_id}", headers=headers)
    assert detail_res.status_code == 200
    d = detail_res.json()
    assert d["party"]["name"] == "Bilal Ahmed"
    assert d["metrics"]["net_balance_pkr"] == 1500.0
    assert len(d["transactions"]) == 2
    assert d["transactions"][0]["running_balance_pkr"] == 2500.0
    assert d["transactions"][1]["running_balance_pkr"] == 1500.0


def test_list_parties_summary_and_filtering(client, auth_headers):
    user_id = "test_party_user_4@example.com"
    headers = auth_headers(user_id)

    # Create 2 customers and 1 supplier
    client.post("/api/parties", json={"name": "Customer 1", "party_type": "customer", "opening_balance": 1000.0}, headers=headers)
    client.post("/api/parties", json={"name": "Customer 2", "party_type": "customer", "opening_balance": 2000.0}, headers=headers)
    client.post("/api/parties", json={"name": "Supplier 1", "party_type": "supplier", "opening_balance": 1500.0, "opening_balance_type": "payable"}, headers=headers)

    # List all
    res = client.get(f"/api/parties/{user_id}", headers=headers)
    assert res.status_code == 200
    data = res.json()
    assert len(data["parties"]) == 3
    assert data["summary"]["total_customers"] == 2
    assert data["summary"]["total_suppliers"] == 1
    assert data["summary"]["total_receivable_pkr"] == 3000.0
    assert data["summary"]["total_payable_pkr"] == 1500.0
    assert data["summary"]["net_balance_pkr"] == 1500.0

    # Filter by party_type=supplier
    sup_res = client.get(f"/api/parties/{user_id}?party_type=supplier", headers=headers)
    assert len(sup_res.json()["parties"]) == 1
    assert sup_res.json()["parties"][0]["name"] == "Supplier 1"


def test_update_and_soft_delete_party(client, auth_headers):
    user_id = "test_party_user_5@example.com"
    headers = auth_headers(user_id)

    # Create party
    p_res = client.post("/api/parties", json={"name": "Imran Khan", "phone": "03001112233"}, headers=headers)
    party_id = p_res.json()["party"]["id"]

    # Patch party
    patch_res = client.patch(
        f"/api/parties/{user_id}/{party_id}",
        json={"name": "Imran Khan Niazi", "phone": "03009998877", "credit_limit": 50000.0},
        headers=headers,
    )
    assert patch_res.status_code == 200
    assert patch_res.json()["party"]["name"] == "Imran Khan Niazi"
    assert patch_res.json()["party"]["phone"] == "03009998877"
    assert patch_res.json()["party"]["credit_limit"] == 50000.0

    # Delete party
    del_res = client.delete(f"/api/parties/{user_id}/{party_id}", headers=headers)
    assert del_res.status_code == 200

    # Verify excluded from active list
    list_res = client.get(f"/api/parties/{user_id}", headers=headers)
    assert len(list_res.json()["parties"]) == 0


# ===========================================================================
# Tier 2: Boundary, Negative & Edge Case Tests
# ===========================================================================

def test_reject_empty_party_name(client, auth_headers):
    user_id = "test_party_edge_1@example.com"
    headers = auth_headers(user_id)

    res = client.post("/api/parties", json={"name": "   ", "party_type": "customer"}, headers=headers)
    assert res.status_code == 400
    assert "required" in res.json()["detail"].lower()


def test_reject_negative_opening_balance(client, auth_headers):
    user_id = "test_party_edge_2@example.com"
    headers = auth_headers(user_id)

    res = client.post("/api/parties", json={"name": "Test Party", "opening_balance": -500.0}, headers=headers)
    assert res.status_code == 400
    assert "non-negative" in res.json()["detail"].lower()


def test_reject_invalid_transaction_amount(client, auth_headers):
    user_id = "test_party_edge_3@example.com"
    headers = auth_headers(user_id)

    p_res = client.post("/api/parties", json={"name": "Valid Party"}, headers=headers)
    party_id = p_res.json()["party"]["id"]

    # Zero amount
    res_zero = client.post(
        f"/api/parties/{user_id}/{party_id}/transactions",
        json={"amount": 0.0, "tx_type": "credit"},
        headers=headers,
    )
    assert res_zero.status_code == 400

    # Negative amount
    res_neg = client.post(
        f"/api/parties/{user_id}/{party_id}/transactions",
        json={"amount": -100.0, "tx_type": "credit"},
        headers=headers,
    )
    assert res_neg.status_code == 400


def test_reject_invalid_tx_type(client, auth_headers):
    user_id = "test_party_edge_4@example.com"
    headers = auth_headers(user_id)

    p_res = client.post("/api/parties", json={"name": "Valid Party 2"}, headers=headers)
    party_id = p_res.json()["party"]["id"]

    res = client.post(
        f"/api/parties/{user_id}/{party_id}/transactions",
        json={"amount": 500.0, "tx_type": "invalid_type"},
        headers=headers,
    )
    assert res.status_code == 400
    assert "credit" in res.json()["detail"].lower()


def test_nonexistent_party_returns_404(client, auth_headers):
    user_id = "test_party_edge_5@example.com"
    headers = auth_headers(user_id)

    assert client.get(f"/api/parties/{user_id}/99999", headers=headers).status_code == 404
    assert client.patch(f"/api/parties/{user_id}/99999", json={"name": "X"}, headers=headers).status_code == 404
    assert client.delete(f"/api/parties/{user_id}/99999", headers=headers).status_code == 404


def test_overpayment_flips_balance_status_to_payable(client, auth_headers):
    user_id = "test_party_edge_6@example.com"
    headers = auth_headers(user_id)

    # Customer owes 1000
    p_res = client.post("/api/parties", json={"name": "Rashid", "opening_balance": 1000.0}, headers=headers)
    party_id = p_res.json()["party"]["id"]

    # Customer pays 1500 (overpayment of 500)
    tx_res = client.post(
        f"/api/parties/{user_id}/{party_id}/transactions",
        json={"amount": 1500.0, "tx_type": "debit"},
        headers=headers,
    )
    assert tx_res.status_code == 201
    bal = tx_res.json()["party_summary"]
    assert bal["net_balance_pkr"] == 500.0
    assert bal["signed_balance_pkr"] == -500.0
    assert bal["balance_status"] == "payable"  # User now owes customer advance


def test_special_characters_and_urdu_party_name(client, auth_headers):
    user_id = "test_party_edge_7@example.com"
    headers = auth_headers(user_id)

    urdu_name = "حاجی محمد عثمان کریانہ اسٹور"
    res = client.post("/api/parties", json={"name": urdu_name, "opening_balance": 2500.0}, headers=headers)
    assert res.status_code == 201
    data = res.json()
    assert data["party"]["name"] == urdu_name
    assert data["balance"]["net_balance_pkr"] == 2500.0


# ===========================================================================
# Tier 3: Cross-Feature, Security & Business Profile Tests
# ===========================================================================

def test_idor_cross_user_party_access_forbidden(client, auth_headers):
    headers_alice = auth_headers("alice@example.com")
    headers_bob = auth_headers("bob@example.com")

    # Bob creates party
    bob_p = client.post("/api/parties", json={"name": "Bob Customer"}, headers=headers_bob).json()
    bob_party_id = bob_p["party"]["id"]

    # Alice tries to read Bob's party
    assert client.get(f"/api/parties/bob@example.com", headers=headers_alice).status_code == 403
    assert client.get(f"/api/parties/bob@example.com/{bob_party_id}", headers=headers_alice).status_code == 403
    assert client.delete(f"/api/parties/bob@example.com/{bob_party_id}", headers=headers_alice).status_code == 403
    assert client.post(f"/api/parties/bob@example.com/{bob_party_id}/transactions", json={"amount": 100.0, "tx_type": "credit"}, headers=headers_alice).status_code == 403


def test_business_profile_crud(client, auth_headers):
    user_id = "test_biz_user@example.com"
    headers = auth_headers(user_id)

    # 1. Fetch initial profile
    get_res = client.get(f"/api/business-profile/{user_id}", headers=headers)
    assert get_res.status_code == 200
    assert get_res.json()["profile"]["business_name"] == "My Business"

    # 2. Update profile
    put_res = client.put(
        f"/api/business-profile/{user_id}",
        json={
            "business_name": "Al-Rehman General Store",
            "owner_name": "Rehman Ullah",
            "phone_number": "03001234567",
            "raast_id": "03001234567",
            "easypaisa_number": "03001234567",
            "jazzcash_number": "03001234567",
            "address": "Shop 5, Ghalla Mandi, Faisalabad",
            "statement_footer": "Thank you for shopping with Al-Rehman!",
        },
        headers=headers,
    )
    assert put_res.status_code == 200
    prof = put_res.json()["profile"]
    assert prof["business_name"] == "Al-Rehman General Store"
    assert prof["owner_name"] == "Rehman Ullah"
    assert prof["raast_id"] == "03001234567"


def test_party_statement_export(client, auth_headers):
    user_id = "test_statement_user@example.com"
    headers = auth_headers(user_id)

    # Setup party and 2 transactions
    p_res = client.post("/api/parties", json={"name": "Hamza Ali", "opening_balance": 1000.0}, headers=headers)
    p_id = p_res.json()["party"]["id"]
    client.post(f"/api/parties/{user_id}/{p_id}/transactions", json={"amount": 2000.0, "tx_type": "credit", "description": "Rice 10kg"}, headers=headers)
    client.post(f"/api/parties/{user_id}/{p_id}/transactions", json={"amount": 1500.0, "tx_type": "debit", "description": "Cash payment"}, headers=headers)

    res = client.get(f"/api/parties/{user_id}/{p_id}/statement", headers=headers)
    assert res.status_code == 200
    stmt = res.json()
    assert stmt["status"] == "success"
    assert stmt["party"]["name"] == "Hamza Ali"
    assert len(stmt["filtered_transactions"]) == 2
    assert stmt["party"]["net_balance_pkr"] == 1500.0


def test_whatsapp_reminder_link_generation(client, auth_headers):
    user_id = "test_reminder_user@example.com"
    headers = auth_headers(user_id)

    # Setup party with phone
    p_res = client.post(
        "/api/parties",
        json={"name": "Zahid Khan", "phone": "0300 555 4444", "opening_balance": 3500.0},
        headers=headers,
    )
    p_id = p_res.json()["party"]["id"]

    res = client.get(f"/api/parties/{user_id}/{p_id}/reminder?lang=ur", headers=headers)
    assert res.status_code == 200
    rem = res.json()
    assert rem["status"] == "success"
    assert "3,500" in rem["formatted_text_ur"]
    assert "Zahid Khan" in rem["formatted_text_ur"]
    assert rem["wa_phone"] == "923005554444"
    assert "https://wa.me/923005554444?text=" in rem["whatsapp_url_ur"]


# ===========================================================================
# Tier 4: Real-World Pakistani MSME Workloads
# ===========================================================================

def test_kiriyana_monthly_khata_cycle(client, auth_headers):
    """
    Scenario: Kiriyana shopkeeper records a month of credit purchases for a family,
    partial repayments on salary day, and generates a verified statement.
    """
    user_id = "kiriyana_shopkeeper@example.com"
    headers = auth_headers(user_id)

    # Day 1: New customer profile with Rs. 1,000 opening debt from previous month
    p_res = client.post(
        "/api/parties",
        json={"name": "Chaudhry Akram", "phone": "03012345678", "opening_balance": 1000.0},
        headers=headers,
    )
    party_id = p_res.json()["party"]["id"]

    # Day 5: 10kg Atta & 2kg Ghee on credit (PKR 3,200)
    client.post(
        f"/api/parties/{user_id}/{party_id}/transactions",
        json={"amount": 3200.0, "tx_type": "credit", "description": "10kg Atta, 2kg Ghee"},
        headers=headers,
    )

    # Day 15: Tea & Milk on credit (PKR 1,400)
    client.post(
        f"/api/parties/{user_id}/{party_id}/transactions",
        json={"amount": 1400.0, "tx_type": "credit", "description": "Dalda Ghee, Tapal Tea"},
        headers=headers,
    )

    # Running balance should be 1000 + 3200 + 1400 = 5600
    d1 = client.get(f"/api/parties/{user_id}/{party_id}", headers=headers).json()
    assert d1["metrics"]["net_balance_pkr"] == 5600.0

    # Day 28 (Salary Day): Chaudhry Akram pays Rs. 5,000 cash wasooli
    client.post(
        f"/api/parties/{user_id}/{party_id}/transactions",
        json={"amount": 5000.0, "tx_type": "debit", "description": "Cash Wasooli on Salary"},
        headers=headers,
    )

    # Remaining balance should be exactly 600 PKR
    d2 = client.get(f"/api/parties/{user_id}/{party_id}", headers=headers).json()
    assert d2["metrics"]["net_balance_pkr"] == 600.0
    assert d2["metrics"]["balance_status"] == "receivable"

    # Export statement and check rows
    stmt = client.get(f"/api/parties/{user_id}/{party_id}/statement", headers=headers).json()
    assert len(stmt["filtered_transactions"]) == 3
    assert stmt["filtered_transactions"][-1]["running_balance_pkr"] == 600.0


def test_supplier_wholesale_khata_polarity(client, auth_headers):
    """
    Scenario: Merchant buys stock on credit from wholesale supplier,
    pays advance / partial payments, and verifies running balance polarity.
    """
    user_id = "retail_shopkeeper@example.com"
    headers = auth_headers(user_id)

    # Create supplier party: Merchant owes Rs. 15,000 to supplier
    p_res = client.post(
        "/api/parties",
        json={
            "name": "Engro Foods Wholesale Distributor",
            "party_type": "supplier",
            "opening_balance": 15000.0,
            "opening_balance_type": "payable",
        },
        headers=headers,
    )
    party_id = p_res.json()["party"]["id"]

    # Merchant pays Rs. 10,000 cash to distributor (tx_type='credit' = gave money to supplier)
    client.post(
        f"/api/parties/{user_id}/{party_id}/transactions",
        json={"amount": 10000.0, "tx_type": "credit", "description": "Cash paid to distributor"},
        headers=headers,
    )
    # Remaining payable = 15000 - 10000 = 5000
    bal1 = client.get(f"/api/parties/{user_id}/{party_id}", headers=headers).json()
    assert bal1["metrics"]["net_balance_pkr"] == 5000.0
    assert bal1["metrics"]["balance_status"] == "payable"

    # Distributor delivers 20 cartons milk on credit (Rs. 25,000) (tx_type='debit' = received stock)
    client.post(
        f"/api/parties/{user_id}/{party_id}/transactions",
        json={"amount": 25000.0, "tx_type": "debit", "description": "20 Cartons Olpers Milk"},
        headers=headers,
    )
    # Remaining payable = 5000 + 25000 = 30000
    bal2 = client.get(f"/api/parties/{user_id}/{party_id}", headers=headers).json()
    assert bal2["metrics"]["net_balance_pkr"] == 30000.0
    assert bal2["metrics"]["balance_status"] == "payable"
