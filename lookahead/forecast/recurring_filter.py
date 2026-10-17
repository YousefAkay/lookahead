"""Which recurring series the forecast should not place (DECISIONS.md, 2026-10-12).

(a) A weekly series that is mostly card purchases, and that I did not confirm or add by hand, is
not placed; its rows go back to the everyday-spending stream (the caller takes them out of the
set of series rows), so nothing is lost and nothing is counted twice.

(b) A series whose last real payment is more than 1.5 periods of its cadence before the data's
last day is "possibly ended": it is not placed, and never placed on the first forecast day as
"due and not seen yet". It comes back by itself when a new payment arrives, because the rule is
read again from the payments posted by the data's last day (rule zero). Its rows stay out of the
everyday-spending stream, so a cancelled payment is not drawn at random either.

Never possibly ended: a series I confirmed or added by hand (or edited), a series with a rent
rule, a series with no payment found. Semimonthly is left alone. `ForecastOptions.recurring_fix`
holds a `RecurringFix` (None switches the whole fix off).
"""

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date

from lookahead.recurring_store import Stored

PERIOD_DAYS = {"weekly": 7, "biweekly": 14, "monthly": 30, "yearly": 365}  # not semimonthly
PERIODS_BEFORE_ENDED = 1.5  # strictly more than this many periods since the last payment


@dataclass(frozen=True)
class RecurringFix:
    """Which parts of the recurring-placement fix are on."""

    possibly_ended: bool = True  # (b)
    weekly_card: bool = False  # (a)


DEFAULT_FIX = RecurringFix(possibly_ended=True, weekly_card=True)  # the default (2026-10-12)


def last_real_payment(s: Stored, posted: Mapping[str, date], data_end: date) -> date | None:
    """The latest payment of the series posted on or before `data_end`, or None."""
    days = [posted[fp] for fp in s.evidence if fp in posted and posted[fp] <= data_end]
    return max(days) if days else None


def is_possibly_ended(s: Stored, posted: Mapping[str, date], data_end: date) -> bool:
    if s.cadence not in PERIOD_DAYS:  # semimonthly: left alone
        return False
    if s.status in ("confirmed", "user-added") or s.edited or s.allowed_cents:
        return False  # my own decisions, and rent, are never second-guessed
    last = last_real_payment(s, posted, data_end)
    if last is None:
        return False  # no payment found: nothing to say it stopped
    return (data_end - last).days > PERIODS_BEFORE_ENDED * PERIOD_DAYS[s.cadence]


def possibly_ended_keys(
    series: Sequence[Stored], posted: Mapping[str, date], data_end: date
) -> frozenset[str]:
    """The keys of the series that are possibly ended as of the data's last day."""
    return frozenset(s.key for s in series if is_possibly_ended(s, posted, data_end))


def is_weekly_card(s: Stored, reasons: Sequence[str]) -> bool:
    """(a): a weekly series of money out whose payments are mostly card purchases, that I did not
    confirm, add by hand or edit."""
    if s.cadence != "weekly" or s.direction != "out":
        return False
    if s.status in ("confirmed", "user-added") or s.edited:
        return False
    # "mostly" is a strict majority (DECISIONS.md, 2026-10-12): a tie is not mostly card purchases
    return bool(reasons) and 2 * Counter(reasons)["card purchase"] > len(reasons)


def weekly_card_keys(series: Sequence[Stored], reason_of: Mapping[str, str]) -> frozenset[str]:
    """The keys of the weekly series of card purchases that are not placed. `reason_of` maps a
    payment's fingerprint to the reason of its flow type ("card purchase", "bill payment"...)."""
    return frozenset(
        s.key for s in series
        if s.status != "rejected"
        and is_weekly_card(s, [reason_of[fp] for fp in s.evidence if fp in reason_of])
    )  # fmt: skip
