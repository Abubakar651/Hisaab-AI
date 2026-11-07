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
    assert "فروخت" in src
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
    log_expense must NOT treat it as a clarification, must NOT overwrite store name with complaint text,
    and must NOT insert a second expense row.
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

    assert res["status"] == "rejected_complaint"

    db = SessionLocal()
    updated_tx = db.query(Transaction).filter(Transaction.id == tx.id).first()
    # The original transaction store_name must NOT have been updated to "doodh"
    assert updated_tx.store_name == "local store"

    all_txs = db.query(Transaction).filter(Transaction.user_id == user_id, Transaction.deleted_at.is_(None)).all()
    assert len(all_txs) == 1
    db.close()


def test_run_agent_429_retry_does_not_duplicate_logged_expense(clean_db, monkeypatch):
    import time
    from unittest.mock import MagicMock
    from langchain_core.messages import AIMessage
    import agent
    from agent import run_agent

    sleep_calls = []
    monkeypatch.setattr(time, "sleep", lambda s: sleep_calls.append(s))
    monkeypatch.delenv("GROQ_API_KEY_2", raising=False)
    monkeypatch.delenv("GROQ_API_KEYS", raising=False)

    attempt_counter = {"count": 0}

    def fake_create_agent(model, tools, system_prompt):
        tool_map = {t.name: t for t in tools}
        mock_agent = MagicMock()

        def fake_invoke(inputs):
            curr_attempt = attempt_counter["count"]
            attempt_counter["count"] += 1
            if curr_attempt == 0:
                tool_map["log_expense"].invoke({"store_name": "imtiaz", "total_amount": 1200.0})
                raise RuntimeError("429 rate_limit exceeded")
            else:
                tool_map["log_expense"].invoke({"store_name": "imtiaz", "total_amount": 1200.0})
                return {"messages": [AIMessage(content="Logged PKR 1,200 at Imtiaz.")]}

        mock_agent.invoke.side_effect = fake_invoke
        return mock_agent

    monkeypatch.setattr(agent, "create_agent", fake_create_agent)

    user_id = "retry_user@example.com"
    reply = run_agent(user_id=user_id, user_text="Spent 1200 at Imtiaz", raw_transcript="Spent 1200 at Imtiaz")
    assert "1,200" in reply or "1200" in reply or "Imtiaz" in reply
    assert len(sleep_calls) == 1

    db = SessionLocal()
    txs = db.query(Transaction).filter(Transaction.user_id == user_id, Transaction.deleted_at.is_(None)).all()
    db.close()
    assert len(txs) == 1


def test_run_agent_fallback_model_does_not_duplicate_logged_expense_or_udhaar(clean_db, monkeypatch):
    import time
    from unittest.mock import MagicMock
    from langchain_core.messages import AIMessage
    import agent
    from agent import run_agent

    monkeypatch.setattr(time, "sleep", lambda x: None)

    agent_call_count = {"count": 0}

    def fake_create_agent(model, tools, system_prompt):
        tool_map = {t.name: t for t in tools}
        mock_agent = MagicMock()
        curr_call = agent_call_count["count"]
        agent_call_count["count"] += 1

        if curr_call == 0:
            # Primary agent
            def fake_invoke_primary(inputs):
                tool_map["log_expense"].invoke({"store_name": "al-fatah", "total_amount": 2500.0})
                raise RuntimeError("ThrottlingException: Too many tokens")

            mock_agent.invoke.side_effect = fake_invoke_primary
        else:
            # Fallback agent
            def fake_invoke_fallback(inputs):
                tool_map["log_expense"].invoke({"store_name": "al fatah store", "total_amount": 2500.0})
                return {"messages": [AIMessage(content="Recorded 2500 PKR.")]}

            mock_agent.invoke.side_effect = fake_invoke_fallback

        return mock_agent

    monkeypatch.setattr(agent, "create_agent", fake_create_agent)

    user_id = "fallback_user@example.com"
    reply = run_agent(user_id=user_id, user_text="Spent 2500 at Al-Fatah", raw_transcript="Spent 2500 at Al-Fatah")
    assert "2500" in reply or "Recorded" in reply

    db = SessionLocal()
    txs = db.query(Transaction).filter(Transaction.user_id == user_id, Transaction.deleted_at.is_(None)).all()
    db.close()
    assert len(txs) == 1



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


def test_get_groq_api_keys_deduplicates_and_preserves_order(monkeypatch):
    """get_groq_api_keys extracts keys in order, ignores empties, and removes duplicates."""
    from agent import get_groq_api_keys

    # When GROQ_API_KEY="key_primary", GROQ_API_KEY_2="key_secondary", GROQ_API_KEYS=""
    monkeypatch.setenv("GROQ_API_KEY", "key_primary")
    monkeypatch.setenv("GROQ_API_KEY_2", "key_secondary")
    monkeypatch.setenv("GROQ_API_KEYS", "")
    assert get_groq_api_keys() == ["key_primary", "key_secondary"]

    # When GROQ_API_KEY="key_primary" and GROQ_API_KEY_2="" (or unset)
    monkeypatch.setenv("GROQ_API_KEY", "key_primary")
    monkeypatch.setenv("GROQ_API_KEY_2", "")
    monkeypatch.delenv("GROQ_API_KEYS", raising=False)
    assert get_groq_api_keys() == ["key_primary"]

    # When GROQ_API_KEY="same_key" and GROQ_API_KEY_2="same_key"
    monkeypatch.setenv("GROQ_API_KEY", "same_key")
    monkeypatch.setenv("GROQ_API_KEY_2", "same_key")
    monkeypatch.delenv("GROQ_API_KEYS", raising=False)
    assert get_groq_api_keys() == ["same_key"]


def test_run_agent_429_rotates_to_second_groq_key_without_duplicate_writes(clean_db, monkeypatch):
    """429 on attempt 0 rotates to GROQ_API_KEY_2 instantly without sleep, maintaining idempotency."""
    import time
    from unittest.mock import MagicMock
    from langchain_core.messages import AIMessage
    import agent
    from agent import run_agent

    monkeypatch.setenv("GROQ_API_KEY", "gsk_test_key_1")
    monkeypatch.setenv("GROQ_API_KEY_2", "gsk_test_key_2")
    monkeypatch.setenv("LLM_PROVIDER", "groq")
    monkeypatch.setattr(agent, "_VALIDATED_MODEL", "qwen/qwen3.8-27b")

    sleep_calls = []
    monkeypatch.setattr(time, "sleep", lambda s: sleep_calls.append(s))

    created_chat_groqs = []

    class MockChatGroq(MagicMock):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.groq_api_key = kwargs.get("groq_api_key")
            created_chat_groqs.append(kwargs.get("groq_api_key"))

    monkeypatch.setattr(agent, "ChatGroq", MockChatGroq)

    agent_instances = []
    attempt_counter = {"count": 0}

    def fake_create_agent(model, tools, system_prompt):
        agent_instances.append(model)
        tool_map = {t.name: t for t in tools}
        mock_agent = MagicMock()

        def fake_invoke(inputs):
            curr_attempt = attempt_counter["count"]
            attempt_counter["count"] += 1
            if curr_attempt == 0:
                tool_map["log_expense"].invoke({"store_name": "imtiaz", "total_amount": 1500.0})
                raise RuntimeError("429 rate_limit exceeded")
            else:
                tool_map["log_expense"].invoke({"store_name": "imtiaz", "total_amount": 1500.0})
                return {"messages": [AIMessage(content="Logged PKR 1,500 at Imtiaz.")]}

        mock_agent.invoke.side_effect = fake_invoke
        return mock_agent

    monkeypatch.setattr(agent, "create_agent", fake_create_agent)

    user_id = "dual_key_user@example.com"
    reply = run_agent(user_id=user_id, user_text="Spent 1500 at Imtiaz", raw_transcript="Spent 1500 at Imtiaz")
    assert "1,500" in reply or "1500" in reply or "Imtiaz" in reply

    # Verify time.sleep was NOT called (instant failover when a backup key exists)
    assert len(sleep_calls) == 0

    # Verify create_agent was rebuilt with a ChatGroq instance using gsk_test_key_2
    assert len(agent_instances) >= 2
    assert getattr(agent_instances[-1], "groq_api_key", None) == "gsk_test_key_2" or "gsk_test_key_2" in created_chat_groqs

    # Verify log_expense hits idempotent cache and Transaction count in DB is 1
    db = SessionLocal()
    txs = db.query(Transaction).filter(Transaction.user_id == user_id, Transaction.deleted_at.is_(None)).all()
    db.close()
    assert len(txs) == 1


def test_transcribe_audio_rotates_to_second_groq_key_on_429(monkeypatch):
    """Whisper transcription failover from primary to secondary Groq key on 429."""
    from unittest.mock import MagicMock
    import agent
    from agent import transcribe_audio

    monkeypatch.setenv("GROQ_API_KEY", "gsk_test_key_1")
    monkeypatch.setenv("GROQ_API_KEY_2", "gsk_test_key_2")

    attempted_keys = []

    class MockGroq:
        def __init__(self, api_key=None, **kwargs):
            self.api_key = api_key
            attempted_keys.append(api_key)
            self.audio = MagicMock()

            def fake_create(**create_kwargs):
                if self.api_key == "gsk_test_key_1":
                    raise RuntimeError("Error code: 429 - rate_limit_exceeded")
                mock_res = MagicMock()
                mock_res.text = "پٹرول 1500"
                mock_res.language = "ur"
                return mock_res

            self.audio.transcriptions.create.side_effect = fake_create

    monkeypatch.setattr(agent, "Groq", MockGroq)

    result = transcribe_audio(b"0" * 200, "voice.webm")
    assert "پٹرول 1500" in result
    assert attempted_keys == ["gsk_test_key_1", "gsk_test_key_2"]

