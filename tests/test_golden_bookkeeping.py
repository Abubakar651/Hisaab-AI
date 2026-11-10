import json
from types import SimpleNamespace

import agent
from agent import build_system_prompt, ensure_urdu_script, get_tools_for_user
from database import Budget, RecurringExpense, Transaction, TransactionItem, UdhaarEntry, UserAccount


def _tool_map(user_id: str, raw_transcript: str = ""):
    tools = get_tools_for_user(user_id, raw_transcript=raw_transcript)
    return {t.name: t for t in tools}


def test_golden_pakistani_expense_parsing_math_and_payment_aliases(clean_db):
    user_id = "golden_exp@example.com"

    # 1. Multi-item grocery summary with total_amount=0 -> deterministic sum 1100.0 PKR and 3 TransactionItem rows
    tmap = _tool_map(user_id, raw_transcript="Imtiaz se milk 440, bread 260, eggs 400")
    res_grocery = json.loads(
        tmap["log_expense"].invoke(
            {
                "store_name": "imtiaz",
                "items": "milk: 440, bread: 260, eggs: 400",
                "total_amount": 0,
                "payment_method": "cash",
            }
        )
    )
    assert res_grocery["status"] == "success"
    assert res_grocery["total_amount_pkr"] == 1100.0
    assert res_grocery["items_count"] == 3

    db = agent.SessionLocal()
    try:
        tx_items = (
            db.query(TransactionItem)
            .filter(TransactionItem.transaction_id == res_grocery["transaction_id"])
            .all()
        )
        assert len(tx_items) == 3
        assert round(sum(i.price for i in tx_items), 2) == 1100.0
    finally:
        db.close()

    # 2. Ghost-item reconciliation (total_amount=500.0 with leftover 2000 PKR ghost item alongside 500 PKR fuel item)
    tmap_fuel = _tool_map(user_id, raw_transcript="500 ka petrol pso se")
    res_fuel = json.loads(
        tmap_fuel["log_expense"].invoke(
            {
                "store_name": "pso",
                "total_amount": 500.0,
                "items": [
                    {"item_name": "groceries from yesterday", "price": 2000.0, "category": "groceries"},
                    {"item_name": "petrol", "price": 500.0, "category": "fuel"},
                ],
                "payment_method": "cash",
            }
        )
    )
    assert res_fuel["status"] == "success"
    assert res_fuel["total_amount_pkr"] == 500.0
    assert res_fuel["items_count"] == 1
    assert res_fuel["items"][0]["item"] == "petrol"
    assert res_fuel["items"][0]["price"] == 500.0

    db = agent.SessionLocal()
    try:
        fuel_items = (
            db.query(TransactionItem)
            .filter(TransactionItem.transaction_id == res_fuel["transaction_id"])
            .all()
        )
        assert len(fuel_items) == 1
        assert fuel_items[0].item_name == "petrol"
        assert fuel_items[0].price == 500.0
    finally:
        db.close()

    # 3. Pakistani payment method detection from raw_transcript when LLM leaves payment_method="cash"
    pm_cases = [
        ("300 روپے ایزی پیسہ کیے", 300.0, "easypaisa"),
        ("350 روپے جاز کیش سے دیے", 350.0, "jazzcash"),
        ("400 روپے راست کے ذریعے بھیجے", 400.0, "bank_transfer"),
        ("Paid 450 via nayapay at cafe", 450.0, "card"),
    ]
    for raw_text, amt, expected_pm in pm_cases:
        tmap_pm = _tool_map(user_id, raw_transcript=raw_text)
        res_pm = json.loads(
            tmap_pm["log_expense"].invoke(
                {
                    "store_name": "cafe",
                    "total_amount": amt,
                    "payment_method": "cash",
                }
            )
        )
        assert res_pm["status"] == "success"
        assert res_pm["payment_method"] == expected_pm

    # 4. Urdu/Roman-Urdu transfer recipient extraction
    tmap_transfer = _tool_map(user_id, raw_transcript="Abid Hussain ko 15000 bank se bheje")
    res_transfer = json.loads(
        tmap_transfer["log_expense"].invoke(
            {
                "store_name": "local store",
                "total_amount": 15000.0,
                "payment_method": "cash",
            }
        )
    )
    assert res_transfer["status"] == "success"
    assert res_transfer["store_name"] == "abid hussain"
    assert res_transfer["payment_method"] == "bank_transfer"


def test_golden_shopkeeper_galla_and_udhaar_lifecycle(clean_db):
    user_id = "golden_shop@example.com"
    tmap = _tool_map(user_id, raw_transcript="aaj 2 bags flour 5400 ki farokht hui")

    # 1. Log a daily sale and an expense
    res_sale = json.loads(
        tmap["log_sale"].invoke(
            {
                "customer_or_note": "daily sale",
                "items": "2 bags flour: 5400",
                "payment_method": "cash",
            }
        )
    )
    assert res_sale["status"] == "success"
    assert res_sale["total_amount_pkr"] == 5400.0

    tmap_exp = _tool_map(user_id, raw_transcript="shop electricity bill 1400 easypaisa")
    res_exp = json.loads(
        tmap_exp["log_expense"].invoke(
            {
                "store_name": "shop electricity bill",
                "total_amount": 1400.0,
                "payment_method": "easypaisa",
            }
        )
    )
    assert res_exp["status"] == "success"
    assert res_exp["total_amount_pkr"] == 1400.0

    # 2. Query expenses & verify sale excluded from total_spent_pkr and Galla net math
    res_query = json.loads(tmap_exp["query_expenses"].invoke({}))
    assert res_query["status"] == "success"
    assert res_query["total_spent_pkr"] == 1400.0
    galla = res_query["galla_summary"]
    assert galla["today_sales_pkr"] == 5400.0
    assert galla["today_expenses_pkr"] == 1400.0
    assert galla["today_net_pkr"] == 4000.0

    # 3. Record customer credit, supplier credit, and partial wasooli
    res_cust = json.loads(
        tmap_exp["manage_udhaar"].invoke(
            {
                "action": "saaman_diya",
                "person_name": "Ali Raza",
                "amount": 2000.0,
                "notes": "atta aur cheeni",
            }
        )
    )
    assert res_cust["status"] == "success"

    res_supp = json.loads(
        tmap_exp["manage_udhaar"].invoke(
            {
                "action": "take_credit",
                "person_name": "Tariq Wholesale",
                "amount": 5000.0,
            }
        )
    )
    assert res_supp["status"] == "success"

    res_settle = json.loads(
        tmap_exp["manage_udhaar"].invoke(
            {
                "action": "settle",
                "person_name": "Ali",
                "amount": 800.0,
            }
        )
    )
    assert res_settle["status"] == "success"
    summary = res_settle["summary"]
    assert summary["total_receivable_pkr"] == 1200.0
    assert summary["total_payable_pkr"] == 5000.0
    assert summary["net_udhaar_pkr"] == -3800.0


def test_golden_urdu_script_guard_rejects_devanagari_and_gurmukhi(monkeypatch):
    llm_calls = []

    class DummyLLM:
        def invoke(self, prompt):
            llm_calls.append(prompt)
            return SimpleNamespace(content="آپ کا 1500 کا خرچہ درج ہو گیا")

    monkeypatch.setattr(agent, "get_chat_llm", lambda *a, **kw: DummyLLM())

    # Pure Urdu Nastaliq and pure English must return unchanged without invoking get_chat_llm
    urdu_text = "آپ کا 1500 روپے کا خرچہ درج ہو گیا ہے۔"
    eng_text = "Logged PKR 1,500 at Imtiaz."
    assert ensure_urdu_script(urdu_text) == urdu_text
    assert ensure_urdu_script(eng_text) == eng_text
    assert len(llm_calls) == 0

    # Devanagari and Gurmukhi must trigger get_chat_llm conversion and return Nastaliq Urdu script
    dev_out = ensure_urdu_script("आपका 1500 का खर्चा दर्ज हो गया")
    assert dev_out == "آپ کا 1500 کا خرچہ درج ہو گیا"
    assert len(llm_calls) == 1

    gur_out = ensure_urdu_script("ਤੁਹਾਡਾ ਖਰਚਾ ਦਰਜ ਹੋ ਗਿਆ")
    assert gur_out == "آپ کا 1500 کا خرچہ درج ہو گیا"
    assert len(llm_calls) == 2


def test_system_prompt_is_compact_under_token_budget():
    prompt_ur = build_system_prompt("ur")
    prompt_en = build_system_prompt("en")

    assert len(prompt_ur) <= 6800
    assert len(prompt_en) <= 6800

    required_markers = [
        "Abu Bakar Iqbal",
        "ابوبکر اقبال",
        "MANDATORY COMPLETE DETAILS REQUIREMENT",
        "CUSTOMER CREDIT SALES VS EXPENSES",
        "CONFIRM BEFORE DELETING",
        "COMPLAINTS & CORRECTIONS",
        "NO PARROTING",
        "dedh hazaar=1500",
        "dhai hazaar=2500",
        "farokht/sale=daily sale",
    ]
    for marker in required_markers:
        assert marker in prompt_ur, f"Missing marker in prompt_ur: {marker}"
        assert marker in prompt_en, f"Missing marker in prompt_en: {marker}"


def test_system_prompt_static_prefix_is_cache_aligned_across_languages():
    prompt_ur = build_system_prompt("ur")
    prompt_en = build_system_prompt("en")
    prompt_none = build_system_prompt(None)

    assert len(prompt_ur) >= 4500
    assert len(prompt_en) >= 4500
    assert len(prompt_none) >= 4500
    assert prompt_ur[:4500] == prompt_en[:4500] == prompt_none[:4500]


def test_udhaar_edit_and_undo_via_tool_and_api(client, auth_headers):
    user_id = "udhaar_edit_user@example.com"
    headers = auth_headers(user_id)

    # 1. Create an Udhaar entry for "Kamran" of 1500.0 PKR (direction="receivable", notes="sugar")
    res = client.post(
        "/api/udhaar",
        data={
            "person_name": "Kamran",
            "amount": 1500.0,
            "direction": "receivable",
            "notes": "sugar",
        },
        headers=headers,
    )
    assert res.status_code == 200
    entry_id = res.json()["entry"]["id"]

    # 2. Call PATCH /api/udhaar/{user_id}/{entry_id}
    patch_res = client.patch(
        f"/api/udhaar/{user_id}/{entry_id}",
        data={
            "amount": 1800.0,
            "notes": "sugar and tea",
            "phone_number": "03001234567",
        },
        headers=headers,
    )
    assert patch_res.status_code == 200
    patched_entry = patch_res.json()["entry"]
    assert patched_entry["amount"] == 1800.0
    assert patched_entry["remaining_pkr"] == 1800.0
    assert patched_entry["notes"] == "sugar and tea"
    assert patched_entry["phone_number"] == "03001234567"

    # 3. Invoke manage_udhaar tool with action="edit"
    tmap = _tool_map(user_id)
    res_tool_edit = json.loads(
        tmap["manage_udhaar"].invoke(
            {
                "action": "edit",
                "person_name": "Kamran",
                "amount": 2000.0,
                "notes": "updated via chat",
            }
        )
    )
    assert res_tool_edit["status"] == "success"
    assert res_tool_edit["entry"]["amount"] == 2000.0

    # 4. Invoke manage_udhaar tool with action="undo"
    res_tool_undo = json.loads(
        tmap["manage_udhaar"].invoke({"action": "undo"})
    )
    assert res_tool_undo["status"] == "success"
    assert res_tool_undo["summary"]["open_count"] == 0


def test_udhaar_edit_rejects_amount_below_already_paid(client, auth_headers):
    user_id = "udhaar_reject_user@example.com"
    headers = auth_headers(user_id)

    # 1. Seed an Udhaar entry of 2000.0 PKR for "Ali" and settle 1200.0 PKR
    res = client.post(
        "/api/udhaar",
        data={
            "person_name": "Ali",
            "amount": 2000.0,
            "direction": "receivable",
        },
        headers=headers,
    )
    assert res.status_code == 200
    entry_id = res.json()["entry"]["id"]

    settle_res = client.post(
        f"/api/udhaar/{user_id}/{entry_id}/settle",
        data={"amount": 1200.0},
        headers=headers,
    )
    assert settle_res.status_code == 200

    # 2. Attempt PATCH with amount=1000.0 (less than paid_amount)
    bad_res = client.patch(
        f"/api/udhaar/{user_id}/{entry_id}",
        data={"amount": 1000.0},
        headers=headers,
    )
    assert bad_res.status_code == 400

    db = agent.SessionLocal()
    try:
        e = db.query(UdhaarEntry).filter(UdhaarEntry.id == entry_id).first()
        assert e.amount == 2000.0
    finally:
        db.close()


def test_udhaar_csv_export_and_full_json_export(client, auth_headers):
    user_id = "export_user@example.com"
    headers = auth_headers(user_id)

    # 1. Create 1 expense (500.0 PKR) and 1 Udhaar entry (person_name="=SUM(1,2)Evil", amount=900.0)
    tmap = _tool_map(user_id)
    res_exp = json.loads(
        tmap["log_expense"].invoke(
            {"store_name": "general store", "total_amount": 500.0, "payment_method": "cash"}
        )
    )
    assert res_exp["status"] == "success"

    res_udh = client.post(
        "/api/udhaar",
        data={
            "person_name": "=SUM(1,2)Evil",
            "amount": 900.0,
            "direction": "receivable",
        },
        headers=headers,
    )
    assert res_udh.status_code == 200

    # 2. Call GET /api/export/{user_id}/udhaar
    res_csv = client.get(f"/api/export/{user_id}/udhaar", headers=headers)
    assert res_csv.status_code == 200
    assert res_csv.text.startswith("\ufeff")
    assert "'=SUM(1,2)Evil" in res_csv.text

    # 3. Call GET /api/export/{user_id}/full
    res_full = client.get(f"/api/export/{user_id}/full", headers=headers)
    assert res_full.status_code == 200
    data = res_full.json()
    assert "transactions" in data
    assert "udhaar" in data
    assert "budgets" in data
    assert "recurring" in data
    assert len(data["transactions"]) >= 1
    assert len(data["udhaar"]) >= 1


def test_delete_my_account_purges_all_tenant_data_with_strict_isolation(client, auth_headers):
    del_user = "delete_me@example.com"
    keep_user = "keep_me@example.com"
    del_headers = auth_headers(del_user)
    keep_headers = auth_headers(keep_user)

    # Seed data for both users
    for uid, h in [(del_user, del_headers), (keep_user, keep_headers)]:
        tmap = _tool_map(uid)
        tmap["log_expense"].invoke({"store_name": "supermarket", "total_amount": 1200.0})
        tmap["set_budget"].invoke({"monthly_limit": 25000.0})
        tmap["add_recurring_expense"].invoke({"title": "internet bill", "amount": 3500.0})
        client.post(
            "/api/udhaar",
            data={"person_name": "Hamza", "amount": 3000.0, "direction": "receivable"},
            headers=h,
        )

    # Call DELETE /api/auth/account for del_user
    del_res = client.delete("/api/auth/account", headers=del_headers)
    assert del_res.status_code == 200
    assert del_res.json()["status"] == "success"

    # Query DB to assert del_user data purged and keep_user intact
    db = agent.SessionLocal()
    try:
        assert db.query(Transaction).filter(Transaction.user_id == del_user).count() == 0
        assert db.query(TransactionItem).filter(
            TransactionItem.transaction_id.in_(
                db.query(Transaction.id).filter(Transaction.user_id == del_user)
            )
        ).count() == 0
        assert db.query(UdhaarEntry).filter(UdhaarEntry.user_id == del_user).count() == 0
        assert db.query(Budget).filter(Budget.user_id == del_user).count() == 0
        assert db.query(RecurringExpense).filter(RecurringExpense.user_id == del_user).count() == 0
        assert db.query(UserAccount).filter(UserAccount.user_id == del_user).count() == 0

        # keep_user intact
        assert db.query(Transaction).filter(Transaction.user_id == keep_user).count() == 1
        assert db.query(UdhaarEntry).filter(UdhaarEntry.user_id == keep_user).count() == 1
        assert db.query(Budget).filter(Budget.user_id == keep_user).count() == 1
        assert db.query(RecurringExpense).filter(RecurringExpense.user_id == keep_user).count() == 1
        assert db.query(UserAccount).filter(UserAccount.user_id == keep_user).count() == 1
    finally:
        db.close()


def test_sw_js_every_respond_with_has_safe_response_fallback():
    with open("static/sw.js", "r", encoding="utf-8") as f:
        sw = f.read()

    assert "CACHE = 'hisaabai-shell-v24'" in sw
    assert "function safeOfflineResponse()" in sw
    assert "new Response(" in sw and ("status: 503" in sw or "status: 504" in sw)
    assert "ignoreSearch: true" in sw

    respond_blocks = sw.split("event.respondWith(")[1:]
    assert len(respond_blocks) == 3
    for block in respond_blocks:
        assert "safeOfflineResponse()" in block
        assert ".catch(" in block


def test_auth_js_uses_popup_first_on_mobile_browsers_to_avoid_storage_partitioning():
    with open("static/auth.js", "r", encoding="utf-8") as f:
        auth_js = f.read()

    fn_start = auth_js.index("async function signInWithGoogle()")
    fn_end = auth_js.index("// Sign out", fn_start)
    fn_body = auth_js[fn_start:fn_end]

    assert "isMobileOrPWA" not in fn_body
    assert "Android|webOS|iPhone|iPad" not in fn_body

    popup_pos = fn_body.index("signInWithPopup(provider)")
    redirect_pos = fn_body.index("signInWithRedirect(provider)")
    catch_pos = fn_body.index("catch (err)")

    assert popup_pos < catch_pos < redirect_pos
    assert (
        "err.code === 'auth/popup-blocked' || err.code === 'auth/cancelled-popup-request' || err.code === 'auth/operation-not-supported-in-this-environment'"
        in fn_body
    )


def test_auth_js_surfaces_hostname_on_unauthorized_domain_and_uses_i18n_toasts():
    with open("static/auth.js", "r", encoding="utf-8") as f:
        auth_js = f.read()

    fn_start = auth_js.index("function mapAuthError(err)")
    fn_end = auth_js.index("function setAuthMessage(", fn_start)
    fn_body = auth_js[fn_start:fn_end]

    assert "'auth/unauthorized-domain'" in fn_body
    assert "window.location.hostname" in fn_body


def test_urdu_spoken_number_normalization_and_asr_aliases():
    from agent import normalize_urdu_spoken_numbers

    assert "600" in normalize_urdu_spoken_numbers("آج میں نے چھے سو کی بوتلیں اور دو سو کی ٹیشو خریدے")
    assert "200" in normalize_urdu_spoken_numbers("آج میں نے چھے سو کی بوتلیں اور دو سو کی ٹیشو خریدے")
    assert "2500" in normalize_urdu_spoken_numbers("گھر کے راشن پر پچی سو اور بجلی کے بل پر آٹھ سو خرچ ہوئے")
    assert "800" in normalize_urdu_spoken_numbers("گھر کے راشن پر پچی سو اور بجلی کے بل پر آٹھ سو خرچ ہوئے")
    assert "2500" in normalize_urdu_spoken_numbers("اسے صحیح کرو گھر کے راشن پر پچیس سو اور بل پر آٹھ سو خرچ ہوئے")
    assert "800 روپے" in normalize_urdu_spoken_numbers("میرے بوتنوں پر آٹ سرُوپے خرچ ہوئے")


def test_zero_price_items_reconciliation_and_store_category_inference(clean_db):
    import analytics
    from database import SessionLocal

    user_id = "edge_items_cat@example.com"
    tmap = _tool_map(user_id, raw_transcript="آج میں نے چھے سو کی بوتلیں اور دو سو کی ٹیشو خریدے")

    # 1. Zero-price line items with total_amount=800 must reconcile item prices to 600 + 200 = 800
    res = json.loads(
        tmap["log_expense"].invoke(
            {
                "store_name": "general store",
                "items": [{"item_name": "bottles", "price": 0.0}, {"item_name": "tissues", "price": 0.0}],
                "total_amount": 800.0,
            }
        )
    )
    assert res["status"] == "success"
    assert res["total_amount_pkr"] == 800.0
    assert sum(it["price"] for it in res["items"]) == 800.0

    # 2. Store-only expenses infer utilities, fuel, and groceries instead of 'other'
    tmap_bill = _tool_map(user_id, raw_transcript="بجلی کے بل پر آٹھ سو خرچ ہوئے")
    res_bill = json.loads(tmap_bill["log_expense"].invoke({"store_name": "electricity bill", "total_amount": 800.0}))
    assert res_bill["items"][0]["category"] == "utilities"

    tmap_fuel = _tool_map(user_id, raw_transcript="پیٹرول پر پندرہ سو خرچ ہوئے")
    res_fuel = json.loads(tmap_fuel["log_expense"].invoke({"store_name": "petrol pump", "total_amount": 1500.0}))
    assert res_fuel["items"][0]["category"] == "fuel"

    from datetime import datetime
    db = SessionLocal()
    try:
        cats = {c["category"]: c["total_pkr"] for c in analytics.category_breakdown(db, user_id, datetime(2020, 1, 1))}
        assert cats.get("groceries") == 800.0
        assert cats.get("utilities") == 800.0
        assert cats.get("fuel") == 1500.0
    finally:
        db.close()


def test_multi_expense_update_targets_matching_store_and_preserves_other_entries(clean_db):
    from database import SessionLocal

    user_id = "edge_multi_update@example.com"
    turn_state = {"attempt": 0, "mutations_by_tool": {}}
    tools = {
        t.name: t
        for t in get_tools_for_user(
            user_id,
            raw_transcript="گھر کے راشن پر پچی سو اور بجلی کے بل پر آٹھ سو خرچ ہوئے",
            turn_state=turn_state,
        )
    }

    # Log 2 expenses in one turn
    r1 = json.loads(tools["log_expense"].invoke({"store_name": "groceries", "total_amount": 500.0}))
    r2 = json.loads(tools["log_expense"].invoke({"store_name": "electricity bill", "total_amount": 800.0}))
    assert r1["store_name"] == "groceries" and r1["total_amount_pkr"] == 500.0
    assert r2["store_name"] == "electricity bill" and r2["total_amount_pkr"] == 800.0

    # Retry on attempt 1 preserves per-call idempotency without colliding
    turn_state["attempt"] = 1
    r1_retry = json.loads(tools["log_expense"].invoke({"store_name": "groceries", "total_amount": 500.0}))
    r2_retry = json.loads(tools["log_expense"].invoke({"store_name": "electricity bill", "total_amount": 800.0}))
    assert r1_retry["store_name"] == "groceries" and r1_retry["total_amount_pkr"] == 500.0
    assert r2_retry["store_name"] == "electricity bill" and r2_retry["total_amount_pkr"] == 800.0

    # Correcting 'groceries' to 2500 updates groceries row without overwriting electricity bill
    tmap_fix = _tool_map(user_id, raw_transcript="اسے صحیح کرو گھر کے راشن پر پچیس سو اور بل پر آٹھ سو خرچ ہوئے")
    res_fix = json.loads(tmap_fix["update_last_expense"].invoke({"store_name": "groceries", "total_amount": 2500.0}))
    assert res_fix["status"] == "success"
    assert res_fix["store_name"] == "groceries"
    assert res_fix["total_amount_pkr"] == 2500.0

    db = SessionLocal()
    try:
        txs = (
            db.query(Transaction)
            .filter(Transaction.user_id == user_id, Transaction.deleted_at.is_(None))
            .order_by(Transaction.id.asc())
            .all()
        )
        assert [(t.store_name, t.total_amount) for t in txs] == [
            ("groceries", 2500.0),
            ("electricity bill", 800.0),
        ]
    finally:
        db.close()


def test_compound_urdu_numbers_sale_guard_and_sale_deletion(clean_db):
    from langchain_core.messages import HumanMessage
    from agent import (
        normalize_urdu_spoken_numbers,
        _ensure_urdu_expense_clauses_executed,
        _reconcile_reply_with_db_truth,
        get_tools_for_user,
    )

    # 1. Compound place-value coalescing ('ایک لاکھ بیس ہزار' -> '120000', 'پان سو' -> '500', 'سمار' -> 'سامان')
    t1 = normalize_urdu_spoken_numbers(
        "آج میں دو ہزار کا سمار لے کر آیا جس میں سے چھ سو کے انڈے، چار سو کے بوتلیں، پان سو کے برینڈ اور پان سو کے رس"
    )
    assert "2000" in t1 and "600" in t1 and "400" in t1 and "500 کے برینڈ" in t1 and "500 کے رس" in t1
    assert "سامان" in t1

    t2 = normalize_urdu_spoken_numbers(
        "میں نے ادھر دو ہزار فرصن ایک ری پچھا تھا جس کی کل عام دن ایک لاکھ بیس ہزار تھی"
    )
    assert "120000" in t2
    assert "آمدن" in t2
    assert "بیچا" in t2

    # 2. Deterministic Urdu Sale Guard & Delete Guard on sales
    user_id = "stone_sale_guard@example.com"
    turn_state = {"attempt": 0, "mutations_by_tool": {}}
    sale_text = normalize_urdu_spoken_numbers(
        "آج میں نے دو ہزار فٹ سنی گرے پتھر پیچھا جس کی کل آمدن ایک لاکھ بیس ہزار تھی اس کو خاتے میں ایڈ کر دو گلے میں ایڈ کر دو اس کو"
    )
    tools = get_tools_for_user(user_id, raw_transcript=sale_text, turn_state=turn_state)
    _ensure_urdu_expense_clauses_executed(sale_text, tools, turn_state)
    assert "log_sale" in turn_state.get("_mutations_list_by_tool", {})
    sale_res = json.loads(turn_state["_mutations_list_by_tool"]["log_sale"][0])
    assert sale_res["total_amount_pkr"] == 120000.0
    assert "سنی گرے پتھر" in sale_res["customer_or_note"]

    # 3. Delete Guard deletes the most recent sale transaction
    del_state = {"attempt": 0, "mutations_by_tool": {}}
    del_tools = get_tools_for_user(user_id, raw_transcript="I'll delete my last entry.", turn_state=del_state)
    _ensure_urdu_expense_clauses_executed("I'll delete my last entry.", del_tools, del_state)
    del_reply = _reconcile_reply_with_db_truth(
        "I cannot delete your last entry because it was a sale.",
        "I'll delete my last entry.",
        del_tools,
        del_state,
        "en",
    )
    assert "Deleted your last sale entry" in del_reply
    assert "120,000" in del_reply

    # 4. Multi-turn sale follow-up ('میں نے آج تین ہزار فٹ بلیک ماربل فروخت کیا' -> 'اس کی فروخت کی آمدن مجھے آٹھ لاکھ روپے ہوئی')
    m1 = normalize_urdu_spoken_numbers("میں نے آج تین ہزار فٹ بلیک ماربل فروخت کیا")
    m2 = normalize_urdu_spoken_numbers("اس کی فروخت کی آمدن مجھے آٹھ لاکھ روپے ہوئی")
    followup_state = {"attempt": 0, "mutations_by_tool": {}}
    followup_tools = get_tools_for_user(user_id, raw_transcript=m2, turn_state=followup_state)
    _ensure_urdu_expense_clauses_executed(
        m2,
        followup_tools,
        followup_state,
        prior_messages=[HumanMessage(content=m1)],
    )
    assert "log_sale" in followup_state.get("_mutations_list_by_tool", {})
    m2_res = json.loads(followup_state["_mutations_list_by_tool"]["log_sale"][0])
    assert m2_res["total_amount_pkr"] == 800000.0
    assert "3000 فٹ بلیک ماربل" in m2_res["customer_or_note"]


def test_mobile_auth_clock_skew_tolerance_and_popup_prewarm(client):
    with open("main.py", "r", encoding="utf-8") as f:
        main_py = f.read()
    assert "clock_skew_in_seconds=60" in main_py
    assert "clock_skew_seconds=60" in main_py

    resp = client.get("/")
    csp = resp.headers.get("Content-Security-Policy", "")
    assert "https://apis.google.com" in csp
    assert "https://accounts.google.com" in csp

    with open("static/auth.js", "r", encoding="utf-8") as f:
        auth_js = f.read()
    assert "_popupRedirectResolver" in auth_js
    assert "signInSyncPromise" in auth_js
    assert "if (currentUser)" in auth_js






