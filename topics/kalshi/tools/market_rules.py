"""What actually settles a contract."""

from __future__ import annotations

from typing import Any

from rsi_arena import Tool, ToolOutput

from ._clients import HISTORY


class MarketRulesTool(Tool):
    name = "market_rules"
    version = 1
    description = (
        "The settlement terms for a market: what decides it, on whose data, "
        "and when.\n\n"
        "Read this before forming a view. Most avoidable losses come from "
        "answering a slightly different question than the one that settles — "
        "a corners market counting only regulation time, a total that includes "
        "own goals, a spread quoted on a line that has moved."
    )
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {"ticker": {"type": "string"}},
        "required": ["ticker"],
    }

    output_schema: dict[str, Any] = {
        "type": "object",
        "description": "The exchange's rules payload, passed through whole.",
        "properties": {"rules_primary": {"type": "string"},
                       "title": {"type": "string"},
                       "close_time": {"type": "string"}},
    }

    def get_tool_output(self, input: dict[str, Any]) -> ToolOutput:
        rules = HISTORY.rules(input["ticker"])
        if not rules:
            return ToolOutput.failed(f"no rules published for {input['ticker']}")
        primary = (rules.get("rules_primary") or rules.get("title") or "").strip()
        return ToolOutput(
            response=(primary[:600] or f"{input['ticker']}: rules returned but empty."),
            raw_output=rules, raw_api_data=rules)
