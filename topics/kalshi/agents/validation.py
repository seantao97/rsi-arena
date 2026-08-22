"""Check a forecast against arithmetic before it is recorded.

Two defects showed up in live running, and neither is a prompt problem — the
model calls the right tools and then transcribes the answer wrongly:

* ``edge_after_fees`` came back as ``-11.0`` and ``-20.0`` where the truth was
  ``-0.110`` and ``-0.215``, and separately as ``-0.03`` where it was ``-0.06``.
  A 100x error looks absurd; a 2x error passes any eyeball check.
* The probability of a "will happen" contract rose while the state was unchanged
  and the clock ran down, which cannot be right.

Both are cheap to catch here because the correct value is derivable. Nothing in
this module asks a model anything.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..fees import breakeven, edge as true_edge


@dataclass
class Check:
    """One forecast, after validation."""

    ok: bool
    corrected: dict
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def clean(self) -> bool:
        return not self.errors and not self.warnings


# Contracts whose probability can only fall while nothing happens: they ask
# whether an event occurs before a deadline, so time passing without it is
# evidence against.
_DECAYING = ("TOTAL", "SCORE", "RFI", "HR", "GOAL", "CORNERS", "KS", "OUTS")


def _decays(ticker: str) -> bool:
    stem = ticker.split("-")[0].upper()
    return any(k in stem for k in _DECAYING)


def validate(forecast: dict, previous: dict | None = None,
             tolerance: float = 0.005) -> Check:
    """Recompute what can be recomputed; compare the rest against the last one.

    ``previous`` is the last forecast on the same ticker. Monotonicity is only
    asserted when the state has not changed — a goal legitimately moves a
    probability in either direction, the clock alone does not.
    """
    out = dict(forecast)
    errors: list[str] = []
    warnings: list[str] = []

    p = out.get("probability")
    px = out.get("market_price")

    if not isinstance(p, (int, float)) or not 0.0 <= p <= 1.0:
        errors.append(f"probability {p!r} is not a number in [0, 1]")
        return Check(False, out, errors, warnings)
    if not isinstance(px, (int, float)) or not 0.0 <= px <= 1.0:
        errors.append(f"market_price {px!r} is not a number in [0, 1]")
        return Check(False, out, errors, warnings)

    # --- the field the model kept getting wrong ---
    computed = true_edge(p, px)
    reported = out.get("edge_after_fees")
    if not isinstance(reported, (int, float)) or abs(reported - computed) > tolerance:
        warnings.append(
            f"edge_after_fees {reported!r} replaced with {computed:+.4f} "
            f"(from probability {p} against price {px})")
        out["edge_after_fees"] = round(computed, 4)
    out["breakeven"] = round(breakeven(px), 4)

    # --- the position must follow from the number ---
    position = (out.get("position") or "").upper()
    stake = out.get("stake_usd") or 0
    if position not in ("YES", "NO", "PASS"):
        errors.append(f"position {position!r} is not YES, NO or PASS")
    if position == "PASS" and stake:
        warnings.append(f"PASS carries a stake of {stake}; zeroed")
        out["stake_usd"] = 0
    if position != "PASS" and computed <= 0:
        errors.append(
            f"position {position} taken on a negative edge ({computed:+.4f})")

    # --- time only moves one way ---
    if previous and _decays(out.get("ticker", "")):
        same_state = (previous.get("score") == out.get("score")
                      and previous.get("period") == out.get("period"))
        prior = previous.get("probability")
        if same_state and isinstance(prior, (int, float)) and p > prior + tolerance:
            warnings.append(
                f"probability rose {prior} -> {p} with the state unchanged; "
                "this contract can only decay while nothing happens")

    return Check(not errors, out, errors, warnings)
