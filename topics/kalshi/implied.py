"""Odds to probability, and the vig removed.

Sportsbook prices are not probabilities. A -110 / -110 market implies 52.4% on
both sides, summing to 104.8% — the 4.8% is the book's margin. Comparing a model
to a raw price systematically overstates edge by roughly half the vig, so
anything comparing Kalshi against a book has to de-vig first.

Pure functions, no API.
"""

from __future__ import annotations


def american_to_prob(odds: float) -> float:
    """American odds to implied probability, vig included."""
    if odds < 0:
        return -odds / (-odds + 100.0)
    return 100.0 / (odds + 100.0)


def prob_to_american(prob: float) -> float:
    """Inverse of ``american_to_prob``."""
    if not 0 < prob < 1:
        raise ValueError("probability must be strictly between 0 and 1")
    return -(prob * 100) / (1 - prob) if prob > 0.5 else (100 * (1 - prob)) / prob


def decimal_to_prob(odds: float) -> float:
    return 1.0 / odds if odds > 0 else 0.0


def overround(probs: list[float]) -> float:
    """How much the quoted probabilities exceed 1. 0.048 is a normal -110 book."""
    return sum(probs) - 1.0


def devig(probs: list[float], method: str = "proportional") -> list[float]:
    """Remove the margin so the set sums to 1.

    ``proportional`` scales every leg by the same factor. It is simple and
    assumes nothing, but it leaves the longshot overpriced: books carry more
    margin on longshots than on favourites.

    ``power`` solves for the exponent that makes the set sum to 1, which shades
    the longshot down and the favourite up — on a -250/+200 line it gives the
    favourite 0.695 against proportional's 0.682. That matches observed closing
    lines better, at the cost of assuming the bias is there.

    Proportional is the default because it is the one that does not assume.
    """
    total = sum(probs)
    if total <= 0:
        return [0.0] * len(probs)
    if method == "proportional":
        return [p / total for p in probs]
    if method == "power":
        lo, hi = 0.5, 3.0
        for _ in range(60):
            k = (lo + hi) / 2
            if sum(p ** k for p in probs) > 1:
                lo = k
            else:
                hi = k
        k = (lo + hi) / 2
        return [p ** k for p in probs]
    raise ValueError(f"unknown de-vig method {method!r}")


def fair_probabilities(american_odds: list[float],
                       method: str = "proportional") -> list[float]:
    """American odds for every outcome to de-vigged probabilities."""
    return devig([american_to_prob(o) for o in american_odds], method)


def kalshi_vs_book(kalshi_price: float, book_odds: list[float],
                   index: int = 0, method: str = "proportional") -> dict:
    """Compare a Kalshi price against a de-vigged book line.

    ``book_odds`` must list every outcome so the vig can be removed;
    ``index`` picks the one the Kalshi contract refers to.
    """
    fair = fair_probabilities(book_odds, method)
    return {
        "kalshi_price": kalshi_price,
        "book_implied_raw": american_to_prob(book_odds[index]),
        "book_fair": fair[index],
        "overround": overround([american_to_prob(o) for o in book_odds]),
        "edge": fair[index] - kalshi_price,
    }
