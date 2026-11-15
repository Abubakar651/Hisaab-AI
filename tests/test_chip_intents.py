"""Tests for structured JSON responses and fixed templates for default chat prompt chips (Issue #2)."""
from datetime import timedelta
from unittest.mock import patch

import pytest
from pydantic import ValidationError

import analytics
import chip_intents
from database import Budget, Transaction, TransactionItem, get_pkt_now


def _add_tx(db, user_id, book_id, amount, tx_type="expense", title="Test", category="groceries", days_ago=0):
    dt = get_pkt_now() - timedelta(days=days_ago)
    tx = Transaction(
        user_id=user_id,
        book_id=book_id,
        tx_type=tx_type,
        store_name=title,
        title=title,
        total_amount=amount,
        payment_method="cash",
        transaction_date=dt,
    )
    db.add(tx)
    db.flush()
    if tx_type == "expense":
        db.add(
            TransactionItem(
                transaction_id=tx.id,
                item_name=title,
                category=category,
                price=amount,
                quantity=1,
            )
        )
    db.commit()
    return tx


def test_spend_summary_month_and_3days(db):
    uid = "chip_spend@example.com"
    book_id = analytics.ensure_default_books(db, uid)["default_book"].id

    # Empty state
    empty_res = chip_intents.build_intent_response(db, uid, book_id, "spend_summary")
    assert empty_res == {
        "intent": "spend_summary",
        "status": "empty",
        "data": {
            "period": "month",
            "total": 0.0,
            "currency": "PKR",
            "txn_count": 0,
            "top_categories": [],
        },
    }

    # Add transactions and a monthly budget
    _add_tx(db, uid, book_id, 20000.0, title="Rent", category="rent", days_ago=0)
    _add_tx(db, uid, book_id, 4500.0, title="Groceries", category="groceries", days_ago=1)
    db.add(Budget(user_id=uid, book_id=book_id, category="total", monthly_limit=60000.0))
    db.commit()

    month_res = chip_intents.build_intent_response(db, uid, book_id, "spend_summary")
    assert month_res["intent"] == "spend_summary"
    assert month_res["status"] == "ok"
    assert month_res["data"]["period"] == "month"
    assert month_res["data"]["total"] == 24500.0
    assert month_res["data"]["txn_count"] == 2
    assert month_res["data"]["top_categories"][0] == {"name": "rent", "amount": 20000.0}
    assert month_res["data"]["budget"] == {"limit": 60000.0, "remaining": 35500.0}

    # 3-day summary via spend_summary_3d and via 3-day message detection
    res_3d = chip_intents.build_intent_response(db, uid, book_id, "spend_summary_3d")
    assert res_3d["intent"] == "spend_summary"
    assert res_3d["status"] == "ok"
    assert res_3d["data"]["period"] == "last_3_days"
    assert res_3d["data"]["total"] == 24500.0
    assert "budget" not in res_3d["data"]

    res_3d_msg = chip_intents.build_intent_response(
        db, uid, book_id, "spend_summary", message="What did I spend in the last 3 days?"
    )
    assert res_3d_msg["data"]["period"] == "last_3_days"


def test_galla_and_udhaar_summaries(db):
    uid = "chip_shop@example.com"
    book_id = analytics.ensure_default_books(db, uid)["default_book"].id

    # Empty Galla & Udhaar
    g_empty = chip_intents.build_intent_response(db, uid, book_id, "galla_summary")
    assert g_empty["status"] == "empty"
    assert g_empty["data"]["cash_in_drawer"] == 0.0

    u_empty = chip_intents.build_intent_response(db, uid, book_id, "udhaar_summary")
    assert u_empty["status"] == "empty"
    assert u_empty["data"]["receivable_total"] == 0.0
    assert u_empty["data"]["payable_total"] == 0.0

    # Populate today's sales + expenses
    _add_tx(db, uid, book_id, 18500.0, tx_type="sale", title="Counter Sale")
    _add_tx(db, uid, book_id, 4200.0, tx_type="expense", title="Shop Supplies", category="other")
    g_ok = chip_intents.build_intent_response(db, uid, book_id, "galla_summary")
    assert g_ok["status"] == "ok"
    assert g_ok["data"]["sales_total"] == 18500.0
    assert g_ok["data"]["expenses_total"] == 4200.0
    assert g_ok["data"]["cash_in_drawer"] == 14300.0

    # Populate Udhaar with multiple entries for same party to verify aggregation
    analytics.add_udhaar_entry(db, uid, "Ali", 5000.0, direction="receivable", book_id=book_id)
    analytics.add_udhaar_entry(db, uid, "Ali", 7000.0, direction="receivable", book_id=book_id)
    analytics.add_udhaar_entry(db, uid, "Supplier X", 8000.0, direction="payable", book_id=book_id)

    u_ok = chip_intents.build_intent_response(db, uid, book_id, "udhaar_summary")
    assert u_ok["status"] == "ok"
    assert u_ok["data"]["receivable_total"] == 12000.0
    assert u_ok["data"]["payable_total"] == 8000.0
    assert u_ok["data"]["receivables"] == [{"party": "Ali", "amount": 12000.0}]
    assert u_ok["data"]["payables"] == [{"party": "Supplier X", "amount": 8000.0}]


def test_budget_set_two_step_and_undo_last(db):
    uid = "chip_actions@example.com"
    book_id = analytics.ensure_default_books(db, uid)["default_book"].id

    # Step 1: chip click asks for amount
    step1 = chip_intents.build_intent_response(
        db, uid, book_id, "budget_set", message="میرا اس مہینے کا بجٹ سیٹ کریں", awaiting_amount=False
    )
    assert step1 == {
        "intent": "budget_set",
        "status": "need_input",
        "data": {"missing": ["amount"]},
    }

    # Step 2: user enters amount -> sets initial budget (no previous_amount)
    step2 = chip_intents.build_intent_response(
        db, uid, book_id, "budget_set", message="50,000", awaiting_amount=True
    )
    assert step2["intent"] == "budget_set"
    assert step2["status"] == "ok"
    assert step2["data"]["amount"] == 50000.0
    assert "previous_amount" not in step2["data"]

    # Updating budget records previous_amount and supports Urdu digits / k suffix
    step3 = chip_intents.build_intent_response(
        db, uid, book_id, "budget_set", message="۶۰k", awaiting_amount=True
    )
    assert step3["status"] == "ok"
    assert step3["data"]["amount"] == 60000.0
    assert step3["data"]["previous_amount"] == 50000.0

    # Undo last when empty vs when a transaction exists
    u_empty = chip_intents.build_intent_response(db, uid, book_id, "undo_last")
    assert u_empty == {"intent": "undo_last", "status": "empty", "data": {}}

    _add_tx(db, uid, book_id, 150.0, tx_type="expense", title="Chai", category="dining")
    u_ok = chip_intents.build_intent_response(db, uid, book_id, "undo_last")
    assert u_ok["intent"] == "undo_last"
    assert u_ok["status"] == "ok"
    assert u_ok["data"]["deleted"]["description"] == "Chai"
    assert u_ok["data"]["deleted"]["amount"] == 150.0


def test_schema_validation_and_llm_retry_and_fallback(db):
    # Extra fields or invalid status must fail validation
    with pytest.raises(ValidationError):
        chip_intents.validate_envelope({"intent": "galla_summary", "status": "invalid", "data": {}})
    with pytest.raises(ValidationError):
        chip_intents.validate_envelope(
            {
                "intent": "galla_summary",
                "status": "ok",
                "data": {
                    "date": "2026-11-15",
                    "sales_total": 100,
                    "expenses_total": 50,
                    "cash_in_drawer": 50,
                    "unexpected": 1,
                },
            }
        )

    # Mock LLM that fails validation on attempt 1, then succeeds on retry with validation error appended
    calls = []

    class FlakyStructuredLLM:
        def with_structured_output(self, schema, **kwargs):
            return self

        def invoke(self, messages):
            calls.append(messages)
            if len(calls) == 1:
                return {"wrong_field": 123}  # triggers extra="forbid" ValidationError
            return {"amount": 75000.0}

    parsed = chip_intents.invoke_structured_llm_with_retry(
        "budget_set",
        "set budget to seventy five thousand",
        chip_intents.BudgetAmountParse,
        llm=FlakyStructuredLLM(),
    )
    assert parsed is not None
    assert parsed.amount == 75000.0
    assert len(calls) == 2
    assert "Validation error:" in calls[1][-1]["content"]

    # Mock LLM that fails both times -> returns None so caller falls back to free-text
    class BrokenStructuredLLM:
        def with_structured_output(self, schema, **kwargs):
            return self

        def invoke(self, messages):
            return {"bad": "payload"}

    failed = chip_intents.invoke_structured_llm_with_retry(
        "budget_set",
        "unparseable",
        chip_intents.BudgetAmountParse,
        llm=BrokenStructuredLLM(),
    )
    assert failed is None

    # Unrelated expense while awaiting_amount=True must NOT set budget to 500; falls back to None
    uid = "chip_fallback@example.com"
    book_id = analytics.ensure_default_books(db, uid)["default_book"].id
    unrelated = chip_intents.build_intent_response(
        db,
        uid,
        book_id,
        "budget_set",
        message="Spent 500 on chai",
        awaiting_amount=True,
        llm=BrokenStructuredLLM(),
    )
    assert unrelated is None


def test_chat_endpoint_routes_chip_and_falls_back(client, auth_headers):
    uid = "chip_api@example.com"
    headers = auth_headers(uid)

    # 1. Chip request returns structured envelope without invoking run_agent
    with patch("main.run_agent") as mock_agent:
        res = client.post(
            "/api/chat",
            data={
                "user_id": uid,
                "message": "What is my total spend this month?",
                "intent_id": "spend_summary",
                "lang": "en",
            },
            headers=headers,
        )
        assert res.status_code == 200
        body = res.json()
        assert body["structured"]["intent"] == "spend_summary"
        assert body["structured"]["status"] == "empty"
        mock_agent.assert_not_called()

    # 2. Unknown intent or failed structured parse falls back cleanly to free-text run_agent
    with patch("main.run_agent", return_value="Logged PKR 500 for chai.") as mock_agent:
        with patch("chip_intents.invoke_structured_llm_with_retry", return_value=None):
            res2 = client.post(
                "/api/chat",
                data={
                    "user_id": uid,
                    "message": "Spent 500 on chai",
                    "intent_id": "budget_set",
                    "awaiting_amount": "true",
                    "lang": "en",
                },
                headers=headers,
            )
            assert res2.status_code == 200
            body2 = res2.json()
            assert "structured" not in body2
            assert body2["reply"] == "Logged PKR 500 for chai."
            mock_agent.assert_called_once()
