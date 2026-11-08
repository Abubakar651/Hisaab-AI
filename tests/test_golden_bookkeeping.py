import json
from types import SimpleNamespace

import agent
from agent import build_system_prompt, ensure_urdu_script, get_tools_for_user
from database import Transaction, TransactionItem


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

