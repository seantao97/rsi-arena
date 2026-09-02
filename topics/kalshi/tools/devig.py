"""Strip a bookmaker's margin out of a price."""

from __future__ import annotations

from typing import Any

from rsi_arena import Tool, ToolOutput

from ..implied import american_to_prob, devig, overround


class DevigOddsTool(Tool):
    name = "devig_odds"
    version = 1
    description = (
        "Remove a bookmaker's margin from a set of American odds and return "
        "probabilities that sum to one.\n\n"
        "Pass every outcome. For soccer that means home, away **and** draw — "
        "de-vigging two legs of a three-way market gives a confidently wrong "
        "answer rather than an error."
    )
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "american_odds": {"type": "array", "items": {"type": "number"},
                              "description": "Every outcome, e.g. [-150, 320, 260]."},
            "method": {"type": "string", "enum": ["proportional", "power"],
                       "default": "proportional"},
        },
        "required": ["american_odds"],
    }

    def get_tool_output(self, input: dict[str, Any]) -> ToolOutput:
        odds = list(input["american_odds"])
        if len(odds) < 2:
            return ToolOutput.failed("pass every outcome, not one")
        raw = [american_to_prob(o) for o in odds]
        method = input.get("method", "proportional")
        fair = devig(raw, method)
        margin = overround(raw)
        return ToolOutput(
            response=(f"De-vigged ({method}): "
                      f"{', '.join(f'{p:.1%}' for p in fair)}. "
                      f"Raw sum {sum(raw):.3f}, margin {margin * 100:+.1f}c."),
            raw_output={"raw": [round(p, 4) for p in raw],
                        "fair": [round(p, 4) for p in fair],
                        "overround": round(margin, 4)})
