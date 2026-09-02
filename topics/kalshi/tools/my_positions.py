"""What the agent is already holding."""

from __future__ import annotations

import json
import pathlib
from typing import Any

from rsi_arena.agent.tools import Tool, ToolOutput

from ._clients import QUOTES


class MyPositionsTool(Tool):
    name = "my_positions"
    version = 1
    description = (
        "Positions currently open, what they cost, and what they are worth "
        "now.\n\n"
        "Without this a decision to hold or close is made blind. The supervisor "
        "keeps the book, and until now the agent could not read it — it was "
        "being asked whether to get out of a position it could not see.\n\n"
        "Reports the entry price, the size, the mark against the live book, and "
        "how far the market has moved since. It does not say what to do; that "
        "is the harness's call."
    )
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "state_dir": {"type": "string", "default": "~/.kalshi-agent",
                          "description": "Where the supervisor keeps its book."},
            "ticker": {"type": "string",
                       "description": "Optional. Just this contract."},
        },
    }
    output_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "positions": {"type": "array", "items": {
                "type": "object",
                "properties": {"ticker": {"type": "string"},
                               "side": {"type": "string", "enum": ["YES", "NO"]},
                               "price": {"type": "number"},
                               "contracts": {"type": "number"},
                               "fee_paid": {"type": "number"},
                               "opened_at": {"type": "string"},
                               "mark": {"type": ["number", "null"],
                                        "description": "Worth per contract now."},
                               "unrealised": {"type": ["number", "null"]}}}},
            "realised_pnl": {"type": "number"},
            "spent_usd": {"type": "number"},
        },
    }

    def get_tool_output(self, input: dict[str, Any]) -> ToolOutput:
        path = pathlib.Path(input.get("state_dir", "~/.kalshi-agent")).expanduser()
        book = path / "state.json"
        if not book.exists():
            return ToolOutput.failed(f"no book at {book}")
        try:
            state = json.loads(book.read_text())
        except json.JSONDecodeError as exc:
            return ToolOutput.failed(f"unreadable book: {exc}")

        only = input.get("ticker")
        rows, realised, spent = [], 0.0, 0.0
        for ticker, pos in (state.get("positions") or {}).items():
            realised += float(pos.get("realised_pnl") or 0.0)
            spent += float(pos.get("spent_usd") or 0.0)
            held = pos.get("holding")
            if not held or (only and ticker != only):
                continue
            row = {"ticker": ticker, **held, "mark": None, "unrealised": None}
            try:
                quote = QUOTES.get_market(ticker)
            except Exception:
                quote = None
            if quote is not None and quote.mid is not None:
                # Worth per contract, on this side, at the live mid.
                mark = quote.mid if held["side"] == "YES" else 1 - quote.mid
                row["mark"] = round(mark, 4)
                row["unrealised"] = round(
                    held["contracts"] * (mark - held["price"]) - held.get("fee_paid", 0),
                    2)
            rows.append(row)

        if not rows:
            return ToolOutput(
                response=(f"No open positions. ${realised:+,.2f} realised so far, "
                          f"${spent:,.2f} spent on model calls."),
                raw_output={"positions": [], "realised_pnl": round(realised, 2),
                            "spent_usd": round(spent, 4)})
        said = "; ".join(
            f"{r['side']} {r['contracts']:,.0f} of {r['ticker'].rsplit('-', 1)[-1]} "
            f"at {r['price']:.3f}"
            + (f", now {r['mark']:.3f} ({r['unrealised']:+,.0f})"
               if r["mark"] is not None else ", unquoted")
            for r in rows)
        return ToolOutput(
            response=f"{len(rows)} open: {said}. ${realised:+,.2f} realised.",
            raw_output={"positions": rows, "realised_pnl": round(realised, 2),
                        "spent_usd": round(spent, 4)})
