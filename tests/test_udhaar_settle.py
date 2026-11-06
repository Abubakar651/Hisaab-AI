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
