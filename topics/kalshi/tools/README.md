# The Kalshi primitive set

Twenty-six tools, one class to a file. Each is a
[`Tool`](../../../rsi_arena/agent/tools.py) subclass: a name, a version, a
description written for a model that has to choose between all of them, a
schema for what goes in and one for what comes out, and a single method that
answers.

Nothing here decides anything. A tool reports; the harness decides. That line
is deliberate — it is what lets a harness be rewritten without rewriting the
things it reads.

## Using them

```python
from topics.kalshi.tools import TOOLS, kalshi_tools

# the structure, for code
TOOLS["market_quote"].get_tool_output({"ticker": "KXEPLGAME-26AUG23NEWLFC-NEW"})
await TOOLS["market_quote"].aget_tool_output(ticker="...")   # same, off the loop

# the sentence, for a model — this is what a ToolStep and a PromptStep call
await TOOLS["market_quote"](ticker="...")                    # -> ToolResult

agent = Agent(..., tools=kalshi_tools())                     # all of them
agent = Agent(..., tools=kalshi_tools(["market_quote",       # only these
                                       "candlesticks",
                                       "previous_trades"]))
```

`kalshi_tools()` returns a `Toolbox`, so a `ToolStep` calls one by name and a
`PromptStep` can hand the whole set to the model and let it choose. Narrowing
the box is worth doing: a harness that cannot reach a tool cannot surprise you
by reaching for it.

## What comes back

Every tool returns a `ToolOutput` with three views of the same answer:

| | |
|---|---|
| `response` | the sentence the model reads — written, not dumped |
| `raw_output` | the structured result, safe for code to index |
| `raw_api_data` | what the upstream service actually sent, where it is worth keeping |

A `ToolResult` carries both: `output` is the structure a pipeline step writes
into run state, and `for_model()` returns the sentence. They are separate
because they are read by different things — a quote is more useful to a model as
`"0.34/0.36, mid 0.350, spread 2c"` than as the JSON those numbers came from,
while the next step in a plan wants the field it can index.

A tool that cannot answer returns `ToolOutput.failed(...)` rather than raising.
The model can read a refusal and try different arguments; it cannot read a
traceback.

## The tools

### What can I bet on

| tool | answers |
|---|---|
| `active_leagues` | which leagues have open markets, and which of those have a score feed. ~15s — ask once |
| `list_markets` | open fixture markets for one league |
| `event_markets` | every market on one fixture: outcomes, spreads, totals, halves |
| `live_markets` | contracts on matches in progress, grouped by match with score and clock |
| `market_rules` | what actually settles a contract |

### What is it worth

| tool | answers |
|---|---|
| `market_quote` | the live book on one contract |
| `order_book` | resting depth, best price first — what size is really there |
| `candlesticks` | the price path as OHLC bars |
| `candlestick_probabilities` | the same fixture de-vigged, so the outcomes sum to one |
| `previous_trades` | the print tape — what traded, not what is quoted |
| `volume_profile` | contracts traded at each price |
| `market_at_time` | the quote as it stood at a past instant |
| `market_settlement` | how it resolved, the closing quote, and CLV on an entry |

### What is happening in the game

| tool | answers |
|---|---|
| `todays_fixtures` | fixtures and the game id the other game tools take |
| `game_state` | score, period, clock |
| `recent_plays` | the last plays, where a feed publishes them |
| `game_context` | venue, weather, injuries, form, head-to-head |
| `sportsbook_line` | the book's price, de-vigged |
| `find_game_for_market` | which fixture a Kalshi event refers to |
| `market_reaction` | how far this contract moved on each scoring play |
| `unexplained_moves` | price moves with no play behind them |

### What a trade costs

| tool | answers |
|---|---|
| `trading_fees` | what a trade costs at a price, and the round trip |
| `price_the_edge` | a probability and a price into an edge after fees, and a stake |
| `coherence_check` | prices on one fixture that cannot all be right |
| `devig_odds` | American odds into probabilities summing to one |
| `kalshi_vs_book` | the exchange against the sportsbook |

## Two things the coverage will not tell you

**Soccer publishes no play-by-play while a match is live.** Not a gap in this
code — the feeds do not carry it. `recent_plays`, `market_reaction` and
`unexplained_moves` all work on baseball and basketball and all return "not
available" on soccer, with the score and clock instead of an error. In-play
soccer is score and clock, and the harnesses here are built around that.

**The fixture feed answers with the next round when a league has nothing on
today.** So seven fixtures across five leagues can mean one match today and
four in three days' time. `todays_fixtures` returns the dates; read them.

## Adding one

A file, a class, and one line in `REGISTRY`.

```python
class SomethingTool(Tool):
    name = "something"
    version = 1
    description = "What it answers, when to reach for it, and when not to."
    input_schema = {"type": "object",
                    "properties": {"ticker": {"type": "string"}},
                    "required": ["ticker"]}

    def get_tool_output(self, input: dict) -> ToolOutput:
        ...
```

The description is the whole interface as far as a model is concerned. Say what
question it answers, what the answer costs, and when it is the wrong tool —
`test_tools.py` holds it between 120 and 1200 characters for exactly that
reason. Write the failure path too: a tool that cannot answer should say why in
a sentence the model can act on.

`version` matters once harnesses are being rewritten. Bump it when the output
shape changes, so a trace records which revision a harness was written against.

## Tests

```
python -m pytest topics/kalshi/tools/
```

Two kinds. The contract tests are structural — every tool declares itself, the
schemas parse, names are unique, required arguments are described, the registry
becomes a toolbox — and need no network. The behaviour tests hit the exchange
on a settled fixture, because a tool returning the wrong shape is a bug the
schema catches and one returning the wrong numbers is not.
