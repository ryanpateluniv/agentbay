"""Double-entry ledger (Blueprint Section 7).

Convention: every leg moves `amount` from one account (a `debit` entry: money leaves)
to another (a `credit` entry: money arrives). balance = sum(credits) - sum(debits).
Each transaction therefore sums to zero by construction. Entries are append-only;
corrections are new transactions. Every transaction has a unique idempotency key, so
retrying any money call returns the original transaction instead of moving money twice.
"""
import threading
from dataclasses import dataclass

from sqlalchemy import case, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session as DB

from ..config import settings
from ..models import LedgerAccount, LedgerEntry, LedgerTxn, PlatformSetting, Session, Wallet, now

BUYER_WALLET = "buyer_wallet"
SESSION_ESCROW = "session_escrow"
CREATOR_PAYABLE = "creator_payable"
PLATFORM_REVENUE = "platform_revenue"
PROVIDER_COST = "provider_cost"
PROMO_LIABILITY = "promo_liability"
PAYMENTS_CLEARING = "payments_clearing"  # money in from the payment provider / out to payouts
PLATFORM = "platform"

# One process-wide writer lock: balance checks and the entries they guard happen atomically.
# (SQLite has a single writer anyway; with Postgres this becomes SELECT ... FOR UPDATE.)
money_lock = threading.RLock()


class LedgerError(Exception):
    pass


class InsufficientCredits(LedgerError):
    pass


@dataclass
class Leg:
    src: LedgerAccount
    dst: LedgerAccount
    amount: int
    memo: str = ""


def account(db: DB, type_: str, owner_id: str = PLATFORM) -> LedgerAccount:
    acc = db.scalar(select(LedgerAccount).where(LedgerAccount.type == type_, LedgerAccount.owner_id == owner_id))
    if acc is None:
        acc = LedgerAccount(type=type_, owner_id=owner_id)
        db.add(acc)
        db.flush()
    return acc


def balance(db: DB, account_id: str) -> int:
    signed = case((LedgerEntry.direction == "credit", LedgerEntry.amount_microcredits), else_=-LedgerEntry.amount_microcredits)
    return int(db.scalar(select(func.coalesce(func.sum(signed), 0)).where(LedgerEntry.account_id == account_id)))


def account_balance(db: DB, type_: str, owner_id: str = PLATFORM) -> int:
    return balance(db, account(db, type_, owner_id).id)


def wallet_balance(db: DB, user_id: str) -> int:
    return account_balance(db, BUYER_WALLET, user_id)


def fee_bps(db: DB) -> int:
    row = db.get(PlatformSetting, "fee_bps")
    return row.value if row else settings.fee_bps


def _refresh_wallet(db: DB, user_id: str):
    w = db.get(Wallet, user_id)
    if w is None:
        w = Wallet(user_id=user_id)
        db.add(w)
    w.cached_balance = wallet_balance(db, user_id)


def post(db: DB, key: str, kind: str, ref_type: str, ref_id: str, legs: list[Leg]) -> tuple[LedgerTxn, bool]:
    """Post a transaction. Returns (txn, created). Caller holds money_lock and commits."""
    existing = db.scalar(select(LedgerTxn).where(LedgerTxn.idempotency_key == key))
    if existing:
        return existing, False
    for leg in legs:
        if leg.amount < 0:
            raise LedgerError(f"negative leg {leg}")
    txn = LedgerTxn(idempotency_key=key, kind=kind, ref_type=ref_type, ref_id=ref_id)
    db.add(txn)
    db.flush()
    ts = now()
    for leg in legs:
        if leg.amount == 0:
            continue
        for acc, direction in ((leg.src, "debit"), (leg.dst, "credit")):
            db.add(LedgerEntry(txn_id=txn.id, account_id=acc.id, direction=direction, amount_microcredits=leg.amount,
                               ref_type=ref_type, ref_id=ref_id, idempotency_key=key, memo=leg.memo, ts=ts))
    db.flush()
    for leg in legs:
        for acc in (leg.src, leg.dst):
            if acc.type == BUYER_WALLET:
                _refresh_wallet(db, acc.owner_id)
    return txn, True


def _commit(db: DB):
    try:
        db.commit()
    except IntegrityError as e:  # concurrent duplicate idempotency key
        db.rollback()
        raise LedgerError("duplicate transaction") from e


# ---------- wallet funding ----------

def grant_promo(db: DB, user_id: str, amount: int, key: str) -> LedgerTxn:
    with money_lock:
        txn, _ = post(db, key, "promo", "user", user_id,
                      [Leg(account(db, PROMO_LIABILITY), account(db, BUYER_WALLET, user_id), amount, "promo grant")])
        _commit(db)
        return txn


def topup(db: DB, user_id: str, amount: int, key: str, purchase_id: str) -> LedgerTxn:
    with money_lock:
        txn, _ = post(db, key, "topup", "credit_purchase", purchase_id,
                      [Leg(account(db, PAYMENTS_CLEARING), account(db, BUYER_WALLET, user_id), amount, "credit pack")])
        _commit(db)
        return txn


# ---------- session money ----------

def escrow_balance(db: DB, session_id: str) -> int:
    return account_balance(db, SESSION_ESCROW, session_id)


def hold(db: DB, s: Session) -> LedgerTxn:
    """Move the cap from the buyer wallet into this session's escrow. 402 if balance is too low."""
    with money_lock:
        key = f"hold:{s.id}"
        if db.scalar(select(LedgerTxn).where(LedgerTxn.idempotency_key == key)) is None:
            if wallet_balance(db, s.buyer_id) < s.cap:
                raise InsufficientCredits("balance below cap")
        txn, _ = post(db, key, "hold", "session", s.id,
                      [Leg(account(db, BUYER_WALLET, s.buyer_id), account(db, SESSION_ESCROW, s.id), s.cap, "hold")])
        _commit(db)
        return txn


def extend(db: DB, s: Session, amount: int, key: str) -> LedgerTxn:
    with money_lock:
        full_key = f"extend:{s.id}:{key}"
        if db.scalar(select(LedgerTxn).where(LedgerTxn.idempotency_key == full_key)) is None:
            if wallet_balance(db, s.buyer_id) < amount:
                raise InsufficientCredits("balance below extension")
            s.cap += amount
        txn, _ = post(db, full_key, "extend", "session", s.id,
                      [Leg(account(db, BUYER_WALLET, s.buyer_id), account(db, SESSION_ESCROW, s.id), amount, "extend")])
        _commit(db)
        return txn


@dataclass
class Split:
    P: int
    C: int
    F: int
    E: int
    released: int
    absorbed: int  # platform-absorbed shortfall when C > P - F

    def as_dict(self):
        return self.__dict__.copy()


def compute_split(price: int, cost: int, escrow: int, fee_rate_bps: int) -> Split:
    P = max(0, min(price, escrow))  # never charge above the held cap
    F = (P * fee_rate_bps + 5000) // 10000  # round half up
    covered = min(cost, P - F)
    E = P - F - covered  # == max(0, P - F - C): creator earnings floor at zero
    return Split(P=P, C=cost, F=F, E=E, released=escrow - P, absorbed=cost - covered)


def settle(db: DB, s: Session, creator_id: str, price: int, cost: int) -> Split:
    """Compute P, C, F, E and empty the escrow. Idempotent per session."""
    with money_lock:
        key = f"settle:{s.id}"
        if db.scalar(select(LedgerTxn).where(LedgerTxn.idempotency_key == key)):
            return Split(P=s.price, C=s.cost, F=s.fee, E=s.creator_earnings,
                         released=0, absorbed=max(0, s.cost - (s.price - s.fee - s.creator_earnings)))
        escrow_acc = account(db, SESSION_ESCROW, s.id)
        split = compute_split(price, cost, balance(db, escrow_acc.id), fee_bps(db))
        provider, revenue = account(db, PROVIDER_COST), account(db, PLATFORM_REVENUE)
        post(db, key, "settle", "session", s.id, [
            Leg(escrow_acc, provider, split.C - split.absorbed, "model and tool cost"),
            Leg(escrow_acc, revenue, split.F, "platform fee"),
            Leg(escrow_acc, account(db, CREATOR_PAYABLE, creator_id), split.E, "creator earnings"),
            Leg(escrow_acc, account(db, BUYER_WALLET, s.buyer_id), split.released, "release unused hold"),
            Leg(revenue, provider, split.absorbed, "platform absorbs shortfall"),
        ])
        s.price, s.cost, s.fee, s.creator_earnings = split.P, split.C, split.F, split.E
        s.settled_at = now()
        _commit(db)
        return split


def refund(db: DB, s: Session, creator_id: str, reason: str) -> int:
    """Refund a settled session's charge P to the buyer. Funded from creator earnings first, then the
    platform fee. Model cost stays with the platform. Idempotent per session."""
    with money_lock:
        key = f"refund:{s.id}"
        if db.scalar(select(LedgerTxn).where(LedgerTxn.idempotency_key == key)):
            return s.price
        wallet = account(db, BUYER_WALLET, s.buyer_id)
        post(db, key, "refund", "session", s.id, [
            Leg(account(db, CREATOR_PAYABLE, creator_id), wallet, s.creator_earnings, "refund: creator earnings"),
            Leg(account(db, PLATFORM_REVENUE), wallet, s.price - s.creator_earnings, "refund: platform"),
        ])
        s.refund_reason = reason
        _commit(db)
        return s.price


def payout(db: DB, creator_id: str, amount: int, payout_id: str) -> LedgerTxn:
    with money_lock:
        if account_balance(db, CREATOR_PAYABLE, creator_id) < amount:
            raise InsufficientCredits("payable below payout")
        txn, _ = post(db, f"payout:{payout_id}", "payout", "payout", payout_id,
                      [Leg(account(db, CREATOR_PAYABLE, creator_id), account(db, PAYMENTS_CLEARING), amount, "payout (simulated)")])
        _commit(db)
        return txn


def history(db: DB, account_id: str, limit: int = 50) -> list[LedgerEntry]:
    return list(db.scalars(select(LedgerEntry).where(LedgerEntry.account_id == account_id)
                           .order_by(LedgerEntry.id.desc()).limit(limit)))
