import threading

import pytest
from sqlalchemy import func, select

from app.config import MICRO
from app.db import SessionLocal
from app.models import LedgerEntry, LedgerTxn, Session
from app.services import ledger
from app.services.ledger import compute_split

C = MICRO  # 1 credit in microcredits


def make_session(db, cap_credits, buyer="usr_sam"):
    s = Session(buyer_id=buyer, agent_version_id="agv_trip_planner_1", cap=cap_credits * C, status="holding")
    db.add(s)
    db.commit()
    return s


def acct(db, t, owner=ledger.PLATFORM):
    return ledger.account_balance(db, t, owner)


def assert_zero_sum(db):
    for txn_id, in db.execute(select(LedgerTxn.id)):
        signed = db.scalar(select(func.sum(LedgerEntry.amount_microcredits * 1)).where(
            LedgerEntry.txn_id == txn_id, LedgerEntry.direction == "credit")) or 0
        debit = db.scalar(select(func.sum(LedgerEntry.amount_microcredits)).where(
            LedgerEntry.txn_id == txn_id, LedgerEntry.direction == "debit")) or 0
        assert signed == debit, txn_id


# --- Section 7 worked examples (credits) ---
@pytest.mark.parametrize("P,Cost,F,E,net", [
    (150, 40, 30, 80, 30),     # per task, normal run
    (150, 180, 30, 0, -30),    # per task, expensive run: E floored, platform absorbs
    (100, 40, 20, 40, 20),     # usage-based, markup 150%
    (0, 25, 0, 0, -25),        # outcome-based, task failed
])
def test_split_examples(P, Cost, F, E, net):
    sp = compute_split(P * C, Cost * C, 200 * C, 2000)
    assert (sp.P, sp.F, sp.E) == (P * C, F * C, E * C)
    assert sp.F - sp.absorbed == net * C


def test_journal_first_example(db):
    """Cap 200, P=150, C=40 -> provider 40, fee 30, creator 80, release 50; escrow back to zero."""
    before = ledger.wallet_balance(db, "usr_sam")
    s = make_session(db, 200)
    ledger.hold(db, s)
    assert ledger.wallet_balance(db, "usr_sam") == before - 200 * C
    assert ledger.escrow_balance(db, s.id) == 200 * C
    split = ledger.settle(db, s, "usr_riya", 150 * C, 40 * C)
    assert (split.C, split.F, split.E, split.released) == (40 * C, 30 * C, 80 * C, 50 * C)
    assert ledger.escrow_balance(db, s.id) == 0
    assert ledger.wallet_balance(db, "usr_sam") == before - 150 * C
    assert acct(db, ledger.PROVIDER_COST) == 40 * C
    assert acct(db, ledger.PLATFORM_REVENUE) == 30 * C
    assert acct(db, ledger.CREATOR_PAYABLE, "usr_riya") == 80 * C
    assert_zero_sum(db)


def test_expensive_run_platform_absorbs(db):
    s = make_session(db, 200)
    ledger.hold(db, s)
    ledger.settle(db, s, "usr_riya", 150 * C, 180 * C)
    assert ledger.escrow_balance(db, s.id) == 0
    assert acct(db, ledger.CREATOR_PAYABLE, "usr_riya") == 0
    assert acct(db, ledger.PROVIDER_COST) == 180 * C
    assert acct(db, ledger.PLATFORM_REVENUE) == -30 * C
    assert_zero_sum(db)


def test_failed_session_full_refund(db):
    before = ledger.wallet_balance(db, "usr_sam")
    s = make_session(db, 200)
    ledger.hold(db, s)
    ledger.settle(db, s, "usr_riya", 0, 25 * C)
    assert ledger.wallet_balance(db, "usr_sam") == before
    assert acct(db, ledger.PLATFORM_REVENUE) == -25 * C
    assert_zero_sum(db)


def test_price_never_exceeds_hold(db):
    s = make_session(db, 100)
    ledger.hold(db, s)
    split = ledger.settle(db, s, "usr_riya", 150 * C, 10 * C)
    assert split.P == 100 * C and ledger.escrow_balance(db, s.id) == 0


def test_hold_and_settle_are_idempotent(db):
    before = ledger.wallet_balance(db, "usr_sam")
    s = make_session(db, 200)
    ledger.hold(db, s)
    ledger.hold(db, s)
    assert ledger.wallet_balance(db, "usr_sam") == before - 200 * C
    ledger.settle(db, s, "usr_riya", 150 * C, 40 * C)
    ledger.settle(db, s, "usr_riya", 150 * C, 40 * C)
    assert ledger.wallet_balance(db, "usr_sam") == before - 150 * C
    assert acct(db, ledger.CREATOR_PAYABLE, "usr_riya") == 80 * C


def test_insufficient_credits(db):
    s = make_session(db, 10_000)  # sam has 5000 credits
    with pytest.raises(ledger.InsufficientCredits):
        ledger.hold(db, s)
    assert ledger.escrow_balance(db, s.id) == 0


def test_concurrent_holds_never_overdraw(db):
    """20 threads each try to hold 400 credits from a 5000-credit wallet: exactly 12 succeed."""
    sids = [make_session(db, 400).id for _ in range(20)]
    ok, errors = [], []

    def worker(sid):
        d = SessionLocal()
        try:
            ledger.hold(d, d.get(Session, sid))
            ok.append(sid)
        except ledger.InsufficientCredits:
            errors.append(sid)
        finally:
            d.close()

    threads = [threading.Thread(target=worker, args=(sid,)) for sid in sids]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert len(ok) == 12 and len(errors) == 8
    assert ledger.wallet_balance(db, "usr_sam") == 5000 * C - 12 * 400 * C >= 0


def test_extend_and_refund(db):
    before = ledger.wallet_balance(db, "usr_sam")
    s = make_session(db, 100)
    ledger.hold(db, s)
    ledger.extend(db, s, 50 * C, "k1")
    ledger.extend(db, s, 50 * C, "k1")  # retried: applied once
    assert ledger.escrow_balance(db, s.id) == 150 * C and s.cap == 150 * C
    ledger.settle(db, s, "usr_riya", 150 * C, 40 * C)
    ledger.refund(db, s, "usr_riya", "agent at fault")
    ledger.refund(db, s, "usr_riya", "agent at fault")
    assert ledger.wallet_balance(db, "usr_sam") == before
    assert acct(db, ledger.CREATOR_PAYABLE, "usr_riya") == 0
    assert acct(db, ledger.PLATFORM_REVENUE) == -40 * C  # platform keeps the model cost loss
    assert_zero_sum(db)


def test_wallet_cache_matches_ledger(db):
    from app.models import Wallet
    s = make_session(db, 200)
    ledger.hold(db, s)
    ledger.settle(db, s, "usr_riya", 150 * C, 40 * C)
    assert db.get(Wallet, "usr_sam").cached_balance == ledger.wallet_balance(db, "usr_sam")
