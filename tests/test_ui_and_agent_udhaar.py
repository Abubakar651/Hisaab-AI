import json
import os
import re
from datetime import datetime, timezone
import pytest

import analytics
from analytics import GENERIC_STORE_NAMES, _format_label
from database import Party, PartyTransaction, Transaction, PendingTransaction
import agent
from agent import get_tools_for_user, _reconcile_reply_with_db_truth


# ===========================================================================
# 1. Legacy Formatting Verification
# ===========================================================================

def test_format_label_cleans_bank_transfer_transfer():
    """Verify _format_label strips underscores and collapses duplicate transfer words."""
    assert _format_label("bank_transfer transfer") == "Bank Transfer"
    assert _format_label("Bank_Transfer Transfer") == "Bank Transfer"
    assert _format_label("bank_transfer") == "Bank Transfer"
    assert _format_label("bank transfer transfer") == "Bank Transfer"
    assert _format_label("easypaisa_transfer transfer") == "Easypaisa Transfer"
    assert _format_label("jazzcash_transfer transfer") == "Jazzcash Transfer"
    assert _format_label("raast_transfer transfer") == "Raast Transfer"
    assert _format_label("transfer transfer") == "Transfer"


def test_generic_store_names_includes_transfer_variations():
    """Verify GENERIC_STORE_NAMES contains bank transfer token combinations."""
    assert "bank_transfer transfer" in GENERIC_STORE_NAMES
    assert "bank_transfer" in GENERIC_STORE_NAMES
    assert "bank transfer" in GENERIC_STORE_NAMES
    assert "transfer" in GENERIC_STORE_NAMES
    assert "easypaisa transfer" in GENERIC_STORE_NAMES
    assert "jazzcash transfer" in GENERIC_STORE_NAMES


def test_transaction_to_dict_display_title_sanitization():
    """Verify Transaction.to_dict() and PendingTransaction.to_dict() sanitize display_title."""
    now = datetime.now(timezone.utc)
    tx1 = Transaction(
        user_id="user_test_fmt@example.com",
        title="Bank_Transfer Transfer",
        store_name="bank_transfer transfer",
        total_amount=5000.0,
        transaction_date=now,
    )
    d1 = tx1.to_dict()
    assert d1["display_title"] == "Bank Transfer"
    assert "_" not in d1["display_title"]
    assert "Transfer Transfer" not in d1["display_title"]

    # Transaction with raw store_name without title
    tx2 = Transaction(
        user_id="user_test_fmt@example.com",
        store_name="easypaisa_transfer transfer",
        total_amount=1200.0,
        transaction_date=now,
    )
    d2 = tx2.to_dict()
    assert d2["display_title"] == "Easypaisa Transfer"
    assert "_" not in d2["display_title"]

    # PendingTransaction to_dict
    ptx = PendingTransaction(
        user_id="user_test_fmt@example.com",
        store_name="jazzcash_transfer transfer",
        total_amount=800.0,
    )
    p_dict = ptx.to_dict()
    assert p_dict["display_title"] == "Jazzcash Transfer"
    assert "_" not in p_dict["display_title"]


def test_api_transactions_store_cleaning(client, auth_headers):
    """Verify /api/transactions returns sanitized store names in breakdowns and items."""
    user_id = "user_api_transfers@example.com"
    headers = auth_headers(user_id)
    now = datetime.now(timezone.utc)

    # Insert a transaction with legacy transfer title/store
    db = agent.SessionLocal()
    try:
        tx = Transaction(
            user_id=user_id,
            title="Bank_Transfer Transfer",
            store_name="bank_transfer transfer",
            total_amount=4500.0,
            transaction_date=now,
        )
        db.add(tx)
        db.commit()
    finally:
        db.close()

    res = client.get(f"/api/transactions/{user_id}", headers=headers)
    assert res.status_code == 200
    data = res.json()
    assert len(data["recent_transactions"]) >= 1
    first_tx = data["recent_transactions"][0]
    assert first_tx["display_title"] == "Bank Transfer"
    assert "_" not in first_tx["display_title"]

    # Check store breakdown
    store_names = [s["store_name"] for s in data.get("store_breakdown", [])]
    for s_name in store_names:
        assert "_" not in s_name
        assert "Transfer Transfer" not in s_name
    if data.get("top_store"):
        assert "_" not in data["top_store"]
        assert "Transfer Transfer" not in data["top_store"]


# ===========================================================================
# 2. Mobile 390px Viewport CSS Assertions
# ===========================================================================

def test_theme_css_book_switcher_and_390px_viewport():
    """Verify theme.css enforces flexible book-switcher-btn width and mobile responsive rules."""
    with open("static/theme.css", "r", encoding="utf-8") as f:
        css = f.read()

    # Verify book-switcher-btn max-width is not the old rigid 106px
    assert "max-width: 106px !important;" not in css
    # Verify flexible width (180px)
    assert "max-width: 180px !important;" in css

    # Verify narrow mobile viewport media query exists (<= 420px covers 390px iPhone viewports)
    assert "@media (max-width: 420px)" in css
    idx = css.find("@media (max-width: 420px)")
    media_section = css[idx:idx + 600]
    assert ".book-switcher-btn" in media_section
    assert ".app-header-left" in media_section
    assert "max-width: 175px !important;" in media_section


def test_theme_css_customer_party_classes():
    """Verify theme.css defines party directory, cards, ledger table, and badges."""
    with open("static/theme.css", "r", encoding="utf-8") as f:
        css = f.read()

    assert ".party-kpi-card" in css
    assert ".party-card" in css
    assert ".party-ledger-table" in css
    assert ".party-badge" in css
    assert ".btn-maine-diye" in css
    assert ".btn-maine-liye" in css


# ===========================================================================
# 3. Modal Escape Key Dismissal Logic
# ===========================================================================

def test_modal_escape_key_bindings_in_scripts():
    """Verify app.js and auth.js bind Escape key to dismiss create-book-modal."""
    with open("static/app.js", "r", encoding="utf-8") as f:
        app_js = f.read()

    with open("static/auth.js", "r", encoding="utf-8") as f:
        auth_js = f.read()

    # In app.js, check that Escape or Esc dismisses create-book-modal and new-book-name handles it
    assert "create-book-modal" in app_js
    assert "new-book-name" in app_js
    assert "Escape" in app_js or "keyCode === 27" in app_js

    # In auth.js, check create-book-modal Escape handling
    assert "create-book-modal" in auth_js
    assert "Escape" in auth_js or "keyCode === 27" in auth_js


def test_index_html_modal_and_party_elements():
    """Verify templates/index.html includes party section, customer profile, and required modals."""
    with open("templates/index.html", "r", encoding="utf-8") as f:
        html = f.read()

    assert 'id="create-book-modal"' in html
    assert 'id="new-book-name"' in html
    assert 'id="parties-section"' in html
    assert 'id="parties-directory-view"' in html
    assert 'id="parties-cards-container"' in html
    assert 'id="party-detail-view"' in html
    assert 'id="party-ledger-tbody"' in html
    assert 'id="add-party-modal"' in html
    assert 'id="add-party-tx-modal"' in html
    assert 'id="party-statement-modal"' in html
    assert 'id="business-profile-modal"' in html


# ===========================================================================
# 4. AI Agent Udhaar & Khata Tool Operations
# ===========================================================================

def test_agent_manage_udhaar_get_balance_customer(clean_db):
    """Verify manage_udhaar with get_balance action returns correct party balance and summary."""
    user_id = "agent_udhaar_user1@example.com"
    db = agent.SessionLocal()
    try:
        # Create a party with opening balance of 2500 receivable
        party = Party(
            user_id=user_id,
            name="Rashid Kiryana",
            phone="03129876543",
            party_type="customer",
            opening_balance=2500.0,
            opening_balance_type="receivable",
        )
        db.add(party)
        db.commit()
    finally:
        db.close()

    tools = {t.name: t for t in get_tools_for_user(user_id)}
    manage_tool = tools["manage_udhaar"]

    res_raw = manage_tool.invoke({"action": "get_balance", "person_name": "Rashid Kiryana"})
    res = json.loads(res_raw)

    assert res["status"] == "success"
    assert res["found"] is True
    assert res["net_balance_pkr"] == 2500.0
    assert res["balance_status"] == "receivable"
    assert "Rashid Kiryana" in res["summary_text"]
    assert res["phone_number"] == "03129876543"


def test_agent_manage_udhaar_get_balance_not_found(clean_db):
    """Verify manage_udhaar with non-existent person returns found=False with clear message."""
    user_id = "agent_udhaar_user2@example.com"
    tools = {t.name: t for t in get_tools_for_user(user_id)}
    manage_tool = tools["manage_udhaar"]

    res_raw = manage_tool.invoke({"action": "get_balance", "person_name": "Unknown Customer XYZ"})
    res = json.loads(res_raw)

    assert res["status"] == "success"
    assert res["found"] is False
    assert res["net_balance_pkr"] == 0.0
    assert "نہیں ملا" in res["summary_text"] or "No khata records found" in res["summary_text"]


def test_agent_manage_udhaar_party_profile_contact(clean_db):
    """Verify manage_udhaar party_profile action returns profile and contact info."""
    user_id = "agent_udhaar_user3@example.com"
    db = agent.SessionLocal()
    try:
        party = Party(
            user_id=user_id,
            name="Usman Goods",
            phone="03009998877",
            party_type="supplier",
            opening_balance=10000.0,
            opening_balance_type="payable",
            credit_limit=50000.0,
        )
        db.add(party)
        db.commit()
    finally:
        db.close()

    tools = {t.name: t for t in get_tools_for_user(user_id)}
    manage_tool = tools["manage_udhaar"]

    res_raw = manage_tool.invoke({"action": "party_profile", "person_name": "Usman Goods"})
    res = json.loads(res_raw)

    assert res["status"] == "success"
    assert res["found"] is True
    assert res["phone_number"] == "03009998877"
    assert res["balance_status"] == "payable"
    assert res["net_balance_pkr"] == 10000.0


def test_agent_manage_udhaar_give_credit_creates_party_and_tx(clean_db):
    """Verify manage_udhaar give_credit adds UdhaarEntry and synchronizes Party and PartyTransaction."""
    user_id = "agent_udhaar_user4@example.com"
    tools = {t.name: t for t in get_tools_for_user(user_id)}
    manage_tool = tools["manage_udhaar"]

    res_raw = manage_tool.invoke({
        "action": "give_credit",
        "person_name": "Kamran Tailor",
        "amount": 1200.0,
        "notes": "Suit stitching credit",
        "phone_number": "03215554433",
    })
    res = json.loads(res_raw)

    assert res["status"] == "success"
    assert res["entry"]["person_name"] == "Kamran Tailor"
    assert res["entry"]["amount"] == 1200.0
    assert res["entry"]["direction"] == "receivable"

    # Verify Party & PartyTransaction created in DB
    db = agent.SessionLocal()
    try:
        party = db.query(Party).filter(Party.user_id == user_id, Party.name == "Kamran Tailor").first()
        assert party is not None
        assert party.party_type == "customer"
        assert party.phone == "03215554433"

        txs = db.query(PartyTransaction).filter(PartyTransaction.party_id == party.id).all()
        assert len(txs) == 1
        assert txs[0].amount == 1200.0
        assert txs[0].tx_type == "credit"
    finally:
        db.close()


def test_agent_manage_udhaar_take_credit_and_settle(clean_db):
    """Verify take_credit records payable and settle updates both legacy udhaar and party ledger."""
    user_id = "agent_udhaar_user5@example.com"
    tools = {t.name: t for t in get_tools_for_user(user_id)}
    manage_tool = tools["manage_udhaar"]

    # 1. Take credit (borrow from supplier)
    res_borrow = json.loads(manage_tool.invoke({
        "action": "take_credit",
        "person_name": "Al Madina Dairy",
        "amount": 3500.0,
        "notes": "Milk crate supply",
    }))
    assert res_borrow["status"] == "success"
    assert res_borrow["entry"]["direction"] == "payable"
    assert res_borrow["entry"]["amount"] == 3500.0

    # 2. Settle 2000 PKR
    res_settle = json.loads(manage_tool.invoke({
        "action": "settle",
        "person_name": "Al Madina Dairy",
        "amount": 2000.0,
        "direction": "payable",
    }))
    assert res_settle["status"] == "success"
    assert res_settle["settled_amount_pkr"] == 2000.0

    # Verify party balance query reflects remaining 1500 payable
    res_bal = json.loads(manage_tool.invoke({
        "action": "get_balance",
        "person_name": "Al Madina Dairy",
    }))
    assert res_bal["found"] is True
    assert res_bal["net_balance_pkr"] == 1500.0
    assert res_bal["balance_status"] == "payable"


def test_agent_reconcile_reply_with_db_truth_udhaar_balance():
    """Verify _reconcile_reply_with_db_truth replaces hallucinated replies with exact DB udhaar balance."""
    turn_state = {
        "_mutations_list_by_tool": {
            "manage_udhaar": [
                json.dumps({
                    "status": "success",
                    "action": "get_balance",
                    "person_name": "Zubair",
                    "net_balance_pkr": 3400.0,
                    "balance_status": "receivable",
                    "balance_text_ur": "آپ نے لینے ہیں",
                    "balance_text_en": "You will receive",
                })
            ]
        }
    }

    # Case 1: Urdu query where LLM hallucinated a wrong amount or vague text
    reconciled_ur = _reconcile_reply_with_db_truth(
        reply_text="زبیر کا کچھ ادھار ہے شاید۔",
        norm_user_text="زبیر کا ادھار کتنا ہے؟",
        tools=[],
        turn_state=turn_state,
        effective_ui_lang="ur",
    )
    assert "3,400" in reconciled_ur
    assert "Zubair" in reconciled_ur
    assert "آپ نے لینے ہیں" in reconciled_ur

    # Case 2: English query
    reconciled_en = _reconcile_reply_with_db_truth(
        reply_text="Zubair has some pending khata.",
        norm_user_text="What is Zubair's balance?",
        tools=[],
        turn_state=turn_state,
        effective_ui_lang="en",
    )
    assert "3,400" in reconciled_en or "3400" in reconciled_en
    assert "Zubair" in reconciled_en
    assert "You will receive" in reconciled_en


def test_agent_manage_udhaar_supplier_settle_syncs_credit_party_tx(clean_db):
    """Settling a supplier without explicit direction records tx_type='credit' (reducing payable)."""
    user_id = "test-supplier-settle@example.com"
    db = agent.SessionLocal()
    try:
        party = Party(
            user_id=user_id,
            name="Bismillah Flour Mill",
            party_type="supplier",
            opening_balance=0.0,
            opening_balance_type="payable",
        )
        db.add(party)
        db.commit()
    finally:
        db.close()

    tools = {t.name: t for t in get_tools_for_user(user_id)}
    manage_tool = tools["manage_udhaar"]

    # Supplier gave stock on credit (take_credit -> payable 5000)
    res_take = json.loads(manage_tool.invoke({
        "action": "take_credit",
        "person_name": "Bismillah Flour Mill",
        "amount": 5000.0,
        "notes": "Atta sacks",
    }))
    assert res_take["status"] == "success"

    # Settle 2000 WITHOUT passing explicit direction -> must infer payable -> PartyTransaction tx_type="credit"
    res_pay = json.loads(manage_tool.invoke({
        "action": "settle",
        "person_name": "Bismillah Flour Mill",
        "amount": 2000.0,
    }))
    assert res_pay["status"] == "success"

    bal = json.loads(manage_tool.invoke({
        "action": "get_balance",
        "person_name": "Bismillah Flour Mill",
    }))
    assert bal["found"] is True
    assert bal["balance_status"] == "payable"
    assert bal["net_balance_pkr"] == 3000.0

    db = agent.SessionLocal()
    try:
        party = db.query(Party).filter(Party.user_id == user_id, Party.name == "Bismillah Flour Mill").first()
        txs = db.query(PartyTransaction).filter(PartyTransaction.party_id == party.id).order_by(PartyTransaction.id.asc()).all()
        assert len(txs) == 2
        assert txs[0].tx_type == "debit"
        assert txs[1].tx_type == "credit"
        assert txs[1].amount == 2000.0
    finally:
        db.close()


def test_polish_compact_header_book_label_and_party_section_rules():
    """Verify compact header book labels, personal-mode hiding for .section-parties, and ledger filter tabs."""
    root = os.path.dirname(os.path.dirname(__file__))
    with open(os.path.join(root, "static", "app.js"), "r", encoding="utf-8") as f:
        app_js = f.read()
    with open(os.path.join(root, "static", "theme.css"), "r", encoding="utf-8") as f:
        css = f.read()
    with open(os.path.join(root, "templates", "index.html"), "r", encoding="utf-8") as f:
        html = f.read()

    assert "function getHeaderBookLabel(" in app_js
    assert "return isEn ? 'My Business' : 'کاروبار';" in app_js
    assert "document.querySelectorAll('.party-ledger-filter-tab')" in app_js
    assert "section-parties" in css and "book-mode-personal" in css
    assert 'id="party-ledger-filter-tabs"' in html
    assert "data-ledger-filter=\"credit\"" in html
    assert "data-ledger-filter=\"debit\"" in html


def test_agent_reconcile_post_mutation_udhaar_rate_limit_fallback():
    """Verify _reconcile_reply_with_db_truth synthesizes accurate confirmation if Step 2 hit 429."""
    # Case 1: Supplier take_credit committed in Step 1, empty reply from 429 Step 2
    ts_take = {
        "_mutations_list_by_tool": {
            "manage_udhaar": [
                json.dumps({
                    "status": "success",
                    "action": "take_credit",
                    "entry": {"person_name": "Metro Wholesale", "amount": 12000.0, "direction": "payable"},
                    "party_balance": {
                        "name": "Metro Wholesale",
                        "net_balance_pkr": 12000.0,
                        "balance_status": "payable",
                        "balance_text_ur": "آپ نے دینے ہیں",
                        "balance_text_en": "You will pay",
                    },
                })
            ]
        }
    }
    msg_ur = _reconcile_reply_with_db_truth("", "Metro Wholesale se 12000 ka maal udhar liya", [], ts_take, "ur")
    assert "Metro Wholesale" in msg_ur
    assert "12,000" in msg_ur
    assert "آپ نے دینے ہیں" in msg_ur

    # Case 2: Customer settle committed in Step 1, empty reply in English mode
    ts_settle = {
        "_mutations_list_by_tool": {
            "manage_udhaar": [
                json.dumps({
                    "status": "success",
                    "action": "settle",
                    "settled_amount_pkr": 1500.0,
                    "person_name": "Haji Akbar",
                    "party_balance": {
                        "name": "Haji Akbar",
                        "net_balance_pkr": 4000.0,
                        "balance_status": "receivable",
                        "balance_text_ur": "آپ نے لینے ہیں",
                        "balance_text_en": "You will receive",
                    },
                })
            ]
        }
    }
    msg_en = _reconcile_reply_with_db_truth("", "Haji Akbar paid 1500 back", [], ts_settle, "en")
    assert "1,500" in msg_en
    assert "4,000" in msg_en
    assert "You will receive" in msg_en


def test_agent_reconcile_catches_hallucinated_udhaar_table_and_tracks_queries(clean_db):
    """Verify Udhaar balance queries track turn_state and block fake LLM numbers (e.g., 15,000 vs 3,500 DB balance)."""
    user_id = "test_udhaar_hallucination@test.com"
    db = agent.SessionLocal()
    try:
        party = Party(
            user_id=user_id,
            name="طاہر بلڈرز",
            party_type="customer",
            opening_balance=2000.0,
            opening_balance_type="receivable",
        )
        db.add(party)
        db.commit()
        db.refresh(party)
        db.add(PartyTransaction(party_id=party.id, user_id=user_id, amount=3000.0, tx_type="credit", description="Simen"))
        db.add(PartyTransaction(party_id=party.id, user_id=user_id, amount=1500.0, tx_type="debit", description="Wasooli"))
        db.commit()
    finally:
        db.close()

    # Case 1: LLM skipped manage_udhaar completely and output a fake table with 15,000
    ts_empty = {"ui_lang": "ur"}
    tools_empty = get_tools_for_user(user_id=user_id, turn_state=ts_empty)
    fake_reply = "| کس کو | رقم (PKR) |\n| :--- | :--- |\n| طاہر بلڈرز | 15,000 |"
    reconciled = _reconcile_reply_with_db_truth(fake_reply, "طاہر بلڈرز کا ادھار کتنا ہے؟", tools_empty, ts_empty, "ur")
    assert "3,500" in reconciled
    assert "15,000" not in reconciled
    assert "طاہر بلڈرز" in reconciled

    # Case 2: manage_udhaar(get_balance) and list populate turn_state and parties list
    ts_query = {"ui_lang": "ur"}
    tools_q = {t.name: t for t in get_tools_for_user(user_id=user_id, turn_state=ts_query)}
    list_res = json.loads(tools_q["manage_udhaar"].invoke({"action": "list"}))
    assert any(p["name"] == "طاہر بلڈرز" and p["net_balance_pkr"] == 3500.0 for p in list_res["parties"])
    assert ts_query.get("udhaar_queried") is True

    bal_res = json.loads(tools_q["manage_udhaar"].invoke({"action": "get_balance", "person_name": "طاہر بلڈرز"}))
    assert bal_res["net_balance_pkr"] == 3500.0
    fixed_after_tool = _reconcile_reply_with_db_truth(
        fake_reply, "طاہر بلڈرز کا ادھار کتنا ہے؟", list(tools_q.values()), ts_query, "ur"
    )
    assert "3,500" in fixed_after_tool
    assert "15,000" not in fixed_after_tool


def test_get_current_user_skips_redundant_last_login_commits(clean_db, monkeypatch):
    """Verify parallel GET requests don't trigger unconditional last_login SQLite write commits."""
    import asyncio
    from types import SimpleNamespace
    import main

    monkeypatch.setattr(
        main,
        "verify_firebase_token_claims",
        lambda _tok: {
            "email": "concurrent_reader@test.com",
            "name": "Concurrent Reader",
            "uid": "uid_concurrent_1",
            "firebase": {"sign_in_provider": "google.com"},
        },
    )

    db = agent.SessionLocal()
    orig_commit = db.commit
    commits = {"count": 0}

    def counted_commit():
        commits["count"] += 1
        return orig_commit()

    monkeypatch.setattr(db, "commit", counted_commit)
    req = SimpleNamespace(headers={"Authorization": "Bearer fake-token"})
    try:
        u1 = asyncio.run(main.get_current_user(req, None, db))
        assert u1.user_id == "concurrent_reader@test.com"
        assert commits["count"] == 1

        # Subsequent GETs within 5 minutes with unchanged profile must NOT acquire SQLite write locks
        u2 = asyncio.run(main.get_current_user(req, None, db))
        u3 = asyncio.run(main.get_current_user(req, None, db))
        assert u2.user_id == "concurrent_reader@test.com"
        assert u3.user_id == "concurrent_reader@test.com"
        assert commits["count"] == 1
    finally:
        db.close()


def test_i18n_ledger_headers_and_non_wrapping_party_actions():
    """Verify missing table header / tooltip i18n keys and unwrapped party action bar classes."""
    root = os.path.dirname(os.path.dirname(__file__))
    with open(os.path.join(root, "static", "i18n.js"), "r", encoding="utf-8") as f:
        i18n_js = f.read()
    with open(os.path.join(root, "templates", "index.html"), "r", encoding="utf-8") as f:
        html = f.read()

    for key in ("'ledger.date'", "'ledger.description'", "'ledger.actions'", "'chat.mic_title'", "'chat.send_title'", "'rec.day_title'"):
        assert i18n_js.count(key) >= 2, f"Missing bilingual entries for {key}"
    assert "whitespace-nowrap shrink-0" in html
    assert 'data-i18n-title="chat.mic_title"' in html
    assert 'data-i18n-title="rec.day_title"' in html


def test_sentry_disabled_in_test_environment():
    """Verify main.py skips Sentry telemetry when APP_ENV=='test' or running under pytest."""
    root = os.path.dirname(os.path.dirname(__file__))
    with open(os.path.join(root, "main.py"), "r", encoding="utf-8") as f:
        src = f.read()

    assert 'if SENTRY_DSN and APP_ENV != "test" and "pytest" not in sys.modules:' in src


def test_jameel_noori_urdu_font_preserved_in_english_mode():
    """Verify Jameel Noori Nastaleeq applies to Urdu text even when UI is switched to English."""
    root = os.path.dirname(os.path.dirname(__file__))
    with open(os.path.join(root, "static", "theme.css"), "r", encoding="utf-8") as f:
        css = f.read()
    with open(os.path.join(root, "static", "app.js"), "r", encoding="utf-8") as f:
        app_js = f.read()

    assert "font-family: 'Jameel Noori Urdu Script';" in css
    assert "unicode-range: U+0600-06FF" in css
    assert "font-weight: 100 900;" in css
    assert '--ui: "Figtree", "Jameel Noori Urdu Script"' in css
    assert '--num: "Fraunces", "Jameel Noori Urdu Script"' in css
    assert "html[lang=\"en\"] .font-ur:not([lang=\"ur\"]):not([dir=\"rtl\"]):not(.has-urdu):not(.font-nastaleeq)" in css
    assert 'dir="rtl" lang="ur"' in app_js


def test_chat_endpoint_existing_user_udhaar_no_sqlite_lock(client, auth_headers, monkeypatch):
    """
    Regression test for Sentry PYTHON-FASTAPI-Z:
    Existing user with default books and 0 orphan rows posting to /api/chat must NOT
    hold an uncommitted SQLite write lock across run_in_threadpool(run_agent),
    so manage_udhaar's separate SessionLocal() succeeds immediately without database lock.
    """
    import database
    import main

    uid = "existing_udhaar_lock@test.com"
    headers = auth_headers(uid)

    # Pre-seed default books so subsequent ensure_default_books has changed=False
    db = agent.SessionLocal()
    try:
        books_info = analytics.ensure_default_books(db, uid)
        biz_book_id = books_info["business_book"].id
    finally:
        db.close()

    # Stub run_agent to invoke the real manage_udhaar tool in the worker thread
    def _fake_run_agent(user_id, user_text, **kwargs):
        tools = get_tools_for_user(
            user_id=user_id,
            raw_transcript=user_text,
            book_id=kwargs.get("book_id"),
            active_book_type=kwargs.get("active_book_type"),
        )
        udhaar_tool = next(t for t in tools if t.name == "manage_udhaar")
        res_json = udhaar_tool.invoke({
            "action": "give_credit",
            "person_name": "Hamza",
            "amount": 2000.0,
            "notes": "saman",
        })
        payload = json.loads(res_json)
        assert payload.get("status") == "success", payload
        return "Hamza ka 2,000 PKR udhaar darj ho gaya."

    monkeypatch.setattr(main, "run_agent", _fake_run_agent)

    resp = client.post(
        "/api/chat",
        data={"message": "Hamza ko 2000 ka saman udhaar diya", "book_id": str(biz_book_id)},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "2,000" in body["reply"]

    # Verify DB persistence
    db = agent.SessionLocal()
    try:
        entry = (
            db.query(database.UdhaarEntry)
            .filter(
                database.UdhaarEntry.user_id == uid,
                database.UdhaarEntry.person_name == "Hamza",
                database.UdhaarEntry.deleted_at.is_(None),
            )
            .first()
        )
        assert entry is not None
        assert entry.amount == 2000.0
        assert entry.notes == "saman"
    finally:
        db.close()
