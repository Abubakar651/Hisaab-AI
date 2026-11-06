import json
import pytest
from database import SessionLocal, Transaction, TransactionItem, UdhaarEntry
from agent import get_tools_for_user, build_system_prompt, transcribe_audio


def test_whisper_domain_prompt_has_generic_bookkeeping_and_no_hardcoded_names():
    """Verify Whisper domain prompt includes retail & bookkeeping terms without hardcoded scenario names."""
    import inspect
    src = inspect.getsource(transcribe_audio)
    # Bookkeeping terms must be present
    assert "ادھار" in src
    assert "کھاتہ" in src
    assert "وصولی" in src
    assert "بکری" in src
    assert "گلا" in src
    # Must NOT hardcode scenario person names
    assert "داؤد" not in src
    assert "dawood" not in src.lower()


def test_manage_udhaar_requires_complete_details(clean_db):
    """Lend and borrow strictly require both person_name and amount, never guessing."""
    user_id = "test_udhaar_details_user@example.com"
    tools = {t.name: t for t in get_tools_for_user(user_id)}
    manage_tool = tools["manage_udhaar"]

    # Missing amount
    res_no_amt = json.loads(manage_tool.invoke({"action": "give_credit", "person_name": "Tariq"}))
    assert res_no_amt["status"] == "error"
    assert "required" in res_no_amt["message"]

    # Missing person_name
    res_no_person = json.loads(manage_tool.invoke({"action": "give_credit", "amount": 1500.0}))
    assert res_no_person["status"] == "error"
    assert "required" in res_no_person["message"]

    # Missing both
    res_no_both = json.loads(manage_tool.invoke({"action": "take_credit"}))
    assert res_no_both["status"] == "error"
    assert "required" in res_no_both["message"]


def test_manage_udhaar_action_aliases(clean_db):
    """Action aliases (khata, credit, saaman_diya) successfully record credit given."""
    user_id = "test_udhaar_aliases_user@example.com"
    tools = {t.name: t for t in get_tools_for_user(user_id)}
    manage_tool = tools["manage_udhaar"]

    res = json.loads(manage_tool.invoke({
        "action": "khata",
        "person_name": "Hamza",
        "amount": 2500.0,
        "notes": "atta aur cheeni",
    }))
    assert res["status"] == "success"
    assert res["entry"]["person_name"] == "Hamza"
    assert res["entry"]["direction"] == "receivable"
    assert res["entry"]["amount"] == 2500.0


def test_complaint_signal_prevents_unwanted_expense_clarification(clean_db):
    """
    If a recent transaction exists and user complains about misclassification or udhaar,
    log_expense must NOT treat it as a clarification or overwrite store name with complaint text.
    """
    user_id = "test_complaint_guard_user@example.com"
    db = SessionLocal()
    # Create recent transaction
    tx = Transaction(
        user_id=user_id,
        store_name="local store",
        total_amount=15000.0,
        payment_method="cash",
    )
    db.add(tx)
    db.commit()
    db.refresh(tx)
    db.close()

    complaint_raw = "یہ خطہ ادار میں ایڈ کرنا تھا تم نے ٹرانزیکشن میں ایڈ کر دیا"
    tools = {t.name: t for t in get_tools_for_user(user_id, raw_transcript=complaint_raw)}
    log_tool = tools["log_expense"]

    # Calling log_expense with complaint text
    res = json.loads(log_tool.invoke({
        "store_name": "doodh",
        "total_amount": 15000.0,
    }))

    db = SessionLocal()
    updated_tx = db.query(Transaction).filter(Transaction.id == tx.id).first()
    # The original transaction store_name must NOT have been updated to "doodh"
    assert updated_tx.store_name == "local store"
    db.close()


def test_system_prompt_instructs_complete_details_and_confirm_before_deleting():
    """Verify system prompt mandates complete udhaar details and confirmation before deletion."""
    prompt_ur = build_system_prompt(ui_lang="ur")
    prompt_en = build_system_prompt(ui_lang="en")

    for prompt in (prompt_ur, prompt_en):
        # Mandatory complete details
        assert "MANDATORY COMPLETE DETAILS REQUIREMENT" in prompt
        # Confirm before deleting
        assert "CONFIRM BEFORE DELETING" in prompt
        # Proactive follow-up suppression during complaints
        assert "COMPLAINTS & CORRECTIONS" in prompt
        # Parroting prohibition
        assert "NO PARROTING" in prompt
