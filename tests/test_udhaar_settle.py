import pytest
from database import UdhaarEntry, get_pkt_now
from analytics import add_udhaar_entry, settle_udhaar


def test_settle_ali_does_not_touch_salim_or_kamali(db):
    user_id = "user_settle_1"
    add_udhaar_entry(db, user_id=user_id, person_name="Salim", amount=1000.0, direction="receivable")
    add_udhaar_entry(db, user_id=user_id, person_name="Kamali Store", amount=1000.0, direction="receivable")
    add_udhaar_entry(db, user_id=user_id, person_name="Ali Raza", amount=1000.0, direction="receivable")

    res = settle_udhaar(db, user_id=user_id, person_name="Ali", payment_amount=500.0)
    assert res["status"] == "success"
    assert res["person_name"] == "Ali Raza"
    assert res["settled_amount_pkr"] == 500.0
    assert res["remaining_balance_pkr"] == 500.0

    db.expire_all()
    rows = {e.person_name: e for e in db.query(UdhaarEntry).filter_by(user_id=user_id).all()}
    assert rows["Ali Raza"].paid_amount == 500.0
    assert rows["Salim"].paid_amount == 0.0
    assert rows["Kamali Store"].paid_amount == 0.0


def test_settle_whole_word_only(db):
    user_id = "user_settle_2"
    add_udhaar_entry(db, user_id=user_id, person_name="Salim", amount=1000.0, direction="receivable")
    add_udhaar_entry(db, user_id=user_id, person_name="Kamali Store", amount=1000.0, direction="receivable")

    res = settle_udhaar(db, user_id=user_id, person_name="Ali", payment_amount=500.0)
    assert res["status"] == "not_found"

    db.expire_all()
    for e in db.query(UdhaarEntry).filter_by(user_id=user_id).all():
        assert e.paid_amount == 0.0
        assert e.status == "open"


def test_exact_name_wins_over_longer_name(db):
    user_id = "user_settle_3"
    add_udhaar_entry(db, user_id=user_id, person_name="Ali Raza", amount=1200.0, direction="receivable")
    add_udhaar_entry(db, user_id=user_id, person_name="Ali", amount=800.0, direction="receivable")

    res = settle_udhaar(db, user_id=user_id, person_name="  ali  ", payment_amount=500.0)
    assert res["status"] == "success"
    assert res["person_name"] == "Ali"
    assert res["settled_amount_pkr"] == 500.0
    assert res["remaining_balance_pkr"] == 300.0

    db.expire_all()
    rows = {e.person_name: e for e in db.query(UdhaarEntry).filter_by(user_id=user_id).all()}
    assert rows["Ali"].paid_amount == 500.0
    assert rows["Ali Raza"].paid_amount == 0.0


def test_ambiguous_names_return_candidates_and_change_nothing(db):
    user_id = "user_settle_4"
    add_udhaar_entry(db, user_id=user_id, person_name="Ali Raza", amount=1000.0, direction="receivable")
    add_udhaar_entry(db, user_id=user_id, person_name="Ali Hassan", amount=1500.0, direction="receivable")

    res = settle_udhaar(db, user_id=user_id, person_name="Ali", payment_amount=500.0)
    assert res["status"] == "ambiguous"
    assert res["candidates"] == ["Ali Hassan", "Ali Raza"]

    db.expire_all()
    for e in db.query(UdhaarEntry).filter_by(user_id=user_id).all():
        assert e.paid_amount == 0.0
        assert e.status == "open"


def test_settle_respects_direction(db):
    user_id = "user_settle_5"
    add_udhaar_entry(db, user_id=user_id, person_name="Bilal", amount=1000.0, direction="payable")
    add_udhaar_entry(db, user_id=user_id, person_name="Bilal", amount=500.0, direction="receivable")

    res = settle_udhaar(
        db,
        user_id=user_id,
        person_name="Bilal",
        payment_amount=500.0,
        direction="receivable",
    )
    assert res["status"] == "success"
    assert res["direction"] == "receivable"
    assert res["settled_amount_pkr"] == 500.0
    assert res["remaining_balance_pkr"] == 0.0

    db.expire_all()
    payable = db.query(UdhaarEntry).filter_by(user_id=user_id, direction="payable").first()
    receivable = db.query(UdhaarEntry).filter_by(user_id=user_id, direction="receivable").first()
    assert payable.paid_amount == 0.0
    assert payable.status == "open"
    assert receivable.paid_amount == 500.0
    assert receivable.status == "settled"


def test_both_directions_without_direction_is_ambiguous(db):
    user_id = "user_settle_6"
    add_udhaar_entry(db, user_id=user_id, person_name="Bilal", amount=1000.0, direction="payable")
    add_udhaar_entry(db, user_id=user_id, person_name="Bilal", amount=500.0, direction="receivable")

    res = settle_udhaar(db, user_id=user_id, person_name="Bilal", payment_amount=500.0)
    assert res["status"] == "ambiguous_direction"
    assert res["receivable_pkr"] == 500.0
    assert res["payable_pkr"] == 1000.0

    db.expire_all()
    for e in db.query(UdhaarEntry).filter_by(user_id=user_id).all():
        assert e.paid_amount == 0.0
        assert e.status == "open"


def test_not_found_lists_open_people(db):
    user_id = "user_settle_7"
    add_udhaar_entry(db, user_id=user_id, person_name="Zain", amount=500.0, direction="receivable")
    add_udhaar_entry(db, user_id=user_id, person_name="Tariq", amount=1000.0, direction="payable")

    res = settle_udhaar(db, user_id=user_id, person_name="Kamran", payment_amount=200.0)
    assert res["status"] == "not_found"
    assert res["open_people"] == ["Tariq", "Zain"]


def test_entry_id_path_unchanged(db):
    user_id = "user_settle_8"
    r1 = add_udhaar_entry(db, user_id=user_id, person_name="Ali Raza", amount=1000.0, direction="receivable")
    add_udhaar_entry(db, user_id=user_id, person_name="Ali Hassan", amount=1500.0, direction="receivable")
    entry_id = r1["entry"]["id"]

    res_partial = settle_udhaar(db, user_id=user_id, entry_id=entry_id, payment_amount=400.0)
    assert res_partial["status"] == "success"
    assert res_partial["person_name"] == "Ali Raza"
    assert res_partial["settled_amount_pkr"] == 400.0
    assert res_partial["remaining_balance_pkr"] == 600.0
    assert res_partial["fully_settled"] is False

    res_full = settle_udhaar(db, user_id=user_id, entry_id=entry_id, payment_amount=None)
    assert res_full["status"] == "success"
    assert res_full["settled_amount_pkr"] == 600.0
    assert res_full["remaining_balance_pkr"] == 0.0
    assert res_full["fully_settled"] is True


def test_other_users_entries_untouched(db):
    victim_res = add_udhaar_entry(db, user_id="victim_user", person_name="Ali", amount=1000.0, direction="receivable")
    victim_id = victim_res["entry"]["id"]

    res_by_name = settle_udhaar(db, user_id="attacker_user", person_name="Ali", payment_amount=500.0)
    assert res_by_name["status"] == "not_found"

    res_by_id = settle_udhaar(db, user_id="attacker_user", entry_id=victim_id, payment_amount=500.0)
    assert res_by_id["status"] == "not_found"

    db.expire_all()
    v_entry = db.query(UdhaarEntry).filter_by(id=victim_id).first()
    assert v_entry.paid_amount == 0.0
    assert v_entry.status == "open"


def test_soft_deleted_entries_ignored(db):
    user_id = "user_settle_10"
    r = add_udhaar_entry(db, user_id=user_id, person_name="Ali", amount=1000.0, direction="receivable")
    entry_id = r["entry"]["id"]

    entry = db.query(UdhaarEntry).filter_by(id=entry_id).first()
    entry.deleted_at = get_pkt_now()
    db.commit()

    res = settle_udhaar(db, user_id=user_id, person_name="Ali", payment_amount=500.0)
    assert res["status"] == "not_found"

    db.expire_all()
    entry_after = db.query(UdhaarEntry).filter_by(id=entry_id).first()
    assert entry_after.paid_amount == 0.0
    assert entry_after.status == "open"


def test_overpayment_not_applied_silently(db):
    user_id = "user_overpay_1"
    add_udhaar_entry(db, user_id=user_id, person_name="Ali", amount=2000.0, direction="receivable")

    res = settle_udhaar(db, user_id=user_id, person_name="Ali", payment_amount=5000.0)
    assert res["status"] == "overpaid"
    assert res["open_balance_pkr"] == 2000.0
    assert res["overpaid_by_pkr"] == 3000.0

    db.expire_all()
    entry = db.query(UdhaarEntry).filter_by(user_id=user_id).first()
    assert entry.paid_amount == 0.0
    assert entry.status == "open"


def test_overpayment_confirmed_settles_open_balance_and_reports_extra(db):
    user_id = "user_overpay_2"
    add_udhaar_entry(db, user_id=user_id, person_name="Ali", amount=2000.0, direction="receivable")

    res = settle_udhaar(
        db,
        user_id=user_id,
        person_name="Ali",
        payment_amount=5000.0,
        confirm_overpay=True,
    )
    assert res["status"] == "success"
    assert res["settled_amount_pkr"] == 2000.0
    assert res["remaining_balance_pkr"] == 0.0
    assert res["overpaid_by_pkr"] == 3000.0
    assert res["fully_settled"] is True

    db.expire_all()
    entry = db.query(UdhaarEntry).filter_by(user_id=user_id).first()
    assert entry.paid_amount == 2000.0
    assert entry.status == "settled"


def test_full_settle_without_amount_still_works(db):
    user_id = "user_full_settle"
    add_udhaar_entry(db, user_id=user_id, person_name="Ali", amount=1200.0, direction="receivable")
    add_udhaar_entry(db, user_id=user_id, person_name="Ali", amount=800.0, direction="receivable")

    res = settle_udhaar(db, user_id=user_id, person_name="Ali", payment_amount=None)
    assert res["status"] == "success"
    assert res["settled_amount_pkr"] == 2000.0
    assert res["remaining_balance_pkr"] == 0.0
    assert res["fully_settled"] is True

    db.expire_all()
    for e in db.query(UdhaarEntry).filter_by(user_id=user_id).all():
        assert e.paid_amount == e.amount
        assert e.status == "settled"


def test_concurrent_settles_never_exceed_amount(db):
    import threading
    from sqlalchemy import event
    import database

    user_id = "user_concurrent_settle"
    add_udhaar_entry(db, user_id=user_id, person_name="Ali", amount=600.0, direction="receivable")

    select_barrier = threading.Barrier(2)
    results = []
    lock = threading.Lock()

    def after_select(conn, cursor, statement, parameters, context, executemany):
        if (
            "SELECT" in statement.upper()
            and "udhaar_entries" in statement
            and threading.current_thread().name.startswith("cas_worker")
        ):
            select_barrier.wait(timeout=5.0)

    event.listen(database.engine, "after_cursor_execute", after_select)

    def worker():
        s = database.SessionLocal()
        try:
            r = settle_udhaar(s, user_id=user_id, person_name="Ali", payment_amount=500.0)
            with lock:
                results.append(r)
        finally:
            s.close()

    try:
        t1 = threading.Thread(target=worker, name="cas_worker_1")
        t2 = threading.Thread(target=worker, name="cas_worker_2")
        t1.start()
        t2.start()
        t1.join(timeout=10.0)
        t2.join(timeout=10.0)
    finally:
        event.remove(database.engine, "after_cursor_execute", after_select)

    db.expire_all()
    entry = db.query(UdhaarEntry).filter_by(user_id=user_id).first()
    assert entry.paid_amount <= 600.0
    assert entry.paid_amount == 500.0
    successes = [r for r in results if r.get("status") == "success"]
    conflicts = [r for r in results if r.get("status") == "conflict"]
    assert len(successes) == 1
    assert successes[0]["settled_amount_pkr"] == 500.0
    assert len(conflicts) == 1


def test_ambiguous_person_across_different_directions_is_caught_even_when_direction_specified(db):
    user_id = "user_ambig_cross_dir"
    add_udhaar_entry(db, user_id=user_id, person_name="Ali Raza", amount=1000.0, direction="payable")
    add_udhaar_entry(db, user_id=user_id, person_name="Ali Hassan", amount=1500.0, direction="receivable")

    res = settle_udhaar(
        db,
        user_id=user_id,
        person_name="Ali",
        payment_amount=500.0,
        direction="receivable",
    )
    assert res["status"] == "ambiguous"
    assert res["candidates"] == ["Ali Hassan", "Ali Raza"]

    db.expire_all()
    for e in db.query(UdhaarEntry).filter_by(user_id=user_id).all():
        assert e.paid_amount == 0.0
        assert e.status == "open"


def test_direction_mismatch_returns_explicit_status(db):
    user_id = "user_dir_mismatch"
    add_udhaar_entry(db, user_id=user_id, person_name="Bilal", amount=1000.0, direction="payable")

    res = settle_udhaar(
        db,
        user_id=user_id,
        person_name="Bilal",
        payment_amount=500.0,
        direction="receivable",
    )
    assert res["status"] == "direction_mismatch"
    assert res["person_name"] == "Bilal"
    assert res["open_directions"] == ["payable"]

    db.expire_all()
    entry = db.query(UdhaarEntry).filter_by(user_id=user_id).first()
    assert entry.paid_amount == 0.0
    assert entry.status == "open"


def test_multi_entry_conflict_rolls_back_earlier_entries_in_loop(db):
    from sqlalchemy import event
    import database

    user_id = "user_multi_rollback"
    r_a = add_udhaar_entry(db, user_id=user_id, person_name="Ali", amount=300.0, direction="receivable")
    r_b = add_udhaar_entry(db, user_id=user_id, person_name="Ali", amount=300.0, direction="receivable")
    id_a = r_a["entry"]["id"]
    id_b = r_b["entry"]["id"]

    triggered = {"done": False}

    def after_select(conn, cursor, statement, parameters, context, executemany):
        if not triggered["done"] and "SELECT" in statement.upper() and "udhaar_entries" in statement:
            triggered["done"] = True
            s2 = database.SessionLocal()
            try:
                s2.query(UdhaarEntry).filter_by(id=id_b).update(
                    {"status": "settled", "paid_amount": 300.0},
                    synchronize_session=False,
                )
                s2.commit()
            finally:
                s2.close()

    event.listen(database.engine, "after_cursor_execute", after_select)
    try:
        res = settle_udhaar(db, user_id=user_id, person_name="Ali", payment_amount=500.0)
    finally:
        event.remove(database.engine, "after_cursor_execute", after_select)

    assert res["status"] == "conflict"

    fresh = database.SessionLocal()
    try:
        entry_a = fresh.query(UdhaarEntry).filter_by(id=id_a).first()
        assert entry_a.paid_amount == 0.0
        assert entry_a.status == "open"
    finally:
        fresh.close()


def test_manage_udhaar_tool_schema_does_not_expose_confirm_overpay(clean_db):
    import json
    import database
    from agent import ManageUdhaarInput, get_tools_for_user

    assert "confirm_overpay" not in ManageUdhaarInput.model_fields

    user_id = "user_no_llm_overpay@example.com"
    s = database.SessionLocal()
    try:
        add_udhaar_entry(s, user_id=user_id, person_name="Ali", amount=2000.0, direction="receivable")
    finally:
        s.close()

    tools = {t.name: t for t in get_tools_for_user(user_id)}
    manage_tool = tools["manage_udhaar"]
    res = json.loads(
        manage_tool.invoke(
            {
                "action": "settle",
                "person_name": "Ali",
                "amount": 5000.0,
                "confirm_overpay": True,
            }
        )
    )
    assert res["status"] == "overpaid"

    s_check = database.SessionLocal()
    try:
        entry = s_check.query(UdhaarEntry).filter_by(user_id=user_id).first()
        assert entry.paid_amount == 0.0
        assert entry.status == "open"
    finally:
        s_check.close()


