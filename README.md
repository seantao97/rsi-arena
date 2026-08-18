# RSI Arena

A Chatbot-Arena-style leaderboard for **agents** instead of models — plus a background loop that uses the arena's own votes to rewrite the agents that lose.

Chatbot Arena answers "which model is better?" But in practice nobody ships a bare model. They ship a *harness*: a model wired to a set of tools, with a prompt that says how and when to use them. Two harnesses over the same model can be far apart in quality. RSI Arena measures that whole package, and then closes the loop — the winners get mutated into the next generation of contenders.

RSI stands for *recursive self-improvement* — see [below](#why-rsi) for what that means here.

## What's being compared

An **agent** here is:

```
agent = LLM + primitives (tools) + orchestration prompt/plan
```

- **LLM** — the underlying model (and its decoding settings).
- **Primitives** — the typed steps the agent may take. For a web-research task the primitive set might be `rewrite_query`, `search`, `scrape`, `select_docs`, `summarize`, `cite`.
- **Orchestration** — how those primitives get combined. Either a *fixed pipeline* (e.g. `rewrite_query → search → select_docs → scrape → summarize`) or a *free-form* agent where the prompt describes the primitives and their tradeoffs and lets the model decide the order and how many times to loop.

Two agents can share an LLM and a primitive set and still differ entirely, because the orchestration differs. That difference is exactly what the arena is built to measure.

## How it works

### 1. Ask

A user picks a **topic** (web research, coding, data analysis, …) and asks a question. Topics matter: the primitive set and the leaderboard are both scoped per topic, since a good web-research harness tells you nothing about a good SQL harness.

### 2. Battle

Two agents are sampled from the topic's active pool and run on the same question, anonymously and side by side. The user sees the answers — and optionally the trace (which primitives fired, in what order, what came back).

### 3. Vote

The user picks a winner, or calls it a tie. That vote is the only supervision signal in the system. Votes feed an Elo / Bradley–Terry rating per agent per topic.

### 4. Optimize (the background job)

Periodically, an offline job:

1. Takes the **top-K agents** for a topic (K ≈ 10) by rating.
2. Gathers evidence for each: its wins and losses, the questions it lost on, and the traces from those battles.
3. Asks each candidate LLM to **rewrite the harness** — change the prompt, reorder or prune the pipeline, add or drop a primitive, adjust the loop budget — given the winners, the losers, and why.
4. Emits the mutated harnesses as a **new generation** of agents, seeded into the pool at a provisional rating.
5. Retires agents that have been decisively and repeatedly beaten.

The next batch of user questions is the evaluation for that generation. Repeat.

```
   users ask ──▶ battles ──▶ votes ──▶ ratings
                    ▲                     │
                    │                     ▼
              new agents ◀── LLM optimizer over top-K
```

## Why RSI

Because nobody writes the agents. We supply the primitives; the agents do the rest.

A normal agent framework works like this: a human reads the traces, notices the search step fires too early, rewrites the prompt, reorders the pipeline, ships it, repeats. The human is the optimizer, and the whole system improves exactly as fast as that human can read transcripts.

RSI Arena removes the human from that inner loop. What we contribute is fixed and small:

- the **primitives** — the raw capabilities an agent may call (`search`, `scrape`, `summarize`, …)
- the **arena** — a way to run two agents on the same question and record which one won

That's it. Nobody hand-writes an orchestration prompt. Nobody decides that `rewrite_query` should come before `search`. Nobody tunes a loop budget. Those are all *discovered*, by the agents, out of the primitive set they were handed.

The recursion is the part that makes it more than plain search. The optimizer is not a separate fixed system sitting outside the arena — it's the same population of LLMs that competes in it:

- Generation *N+1* is **authored by generation *N***. The winners are handed their own traces, their own losses, and their rivals' answers, and asked to write the next harness.
- As the models improve, so does the optimizer, so the harnesses improve faster.
- As the harnesses improve, the bar for winning a battle rises, so the next round of rewrites is judged against stronger opposition.

Each turn of the loop improves the thing that runs the next turn. That's the recursive part: not a model training itself on its own weights, but a population of agents rewriting the code that makes them agents, evaluated by whether the rewrite actually won.

**Where the recursion stops.** Being precise about the bounds, since "self-improving" is an easy word to oversell:

- Model weights never change. The search is over harnesses — prompts, primitive selection, ordering, loop budgets — not parameters.
- The primitive set is human-supplied. An agent can discover a better *way* to use `scrape`; it cannot invent a primitive it wasn't given. (Letting agents propose new primitives is the obvious next step, and the obvious next safety question.)
- Humans still supply the fitness signal, one vote at a time. The system optimizes toward what arena users prefer — which means it inherits their taste, and their biases, exactly.

## Why this might work

- **The signal is real.** Preference votes on real questions people actually cared about, not a static benchmark that gets memorized and gamed.
- **The search space is the right one.** Prompt-and-pipeline space is where most practical agent quality lives, and it's cheap to search — no training, no gradients, just generation and evaluation.
- **The optimizer is diverse.** Every LLM in the pool proposes mutations, so the population isn't shaped by a single model's blind spots.

## Open questions

Things this design has not settled, listed honestly:

- **Vote sparsity.** Elo over a pool that keeps churning needs a lot of battles per agent. Pool size, generation cadence, and matchmaking (favoring high-uncertainty pairings) all have to be tuned against real traffic.
- **Cost asymmetry.** A harness that calls 40 tools will often beat one that calls 3. Ratings probably need to be reported against a cost/latency budget, or split into tiers.
- **Convergence vs. collapse.** Top-K selection plus LLM mutation can converge on one local style. Some diversity pressure — novelty bonuses, protected niches, occasional random restarts — is likely needed.
- **Adversarial voting.** Anything with a leaderboard attracts vote manipulation.
- **Trace visibility.** Showing traces makes votes better informed but also biases users toward agents that *look* thorough.

## Running it

The [runtime](rsi_arena/) is built: the layer agents are made of. The arena itself — battles,
votes, ratings, the optimizer loop — is not.

```bash
git clone https://github.com/Helpigent/rsi-arena && cd rsi-arena
pip install -e .                         # httpx and pydantic, nothing else

export OPENROUTER_API_KEY=sk-or-...      # every model goes through OpenRouter
export SEARCHAPI_API_KEY=...             # optional: Google search for the research agents
```

Three sample agents ship with it. They answer the same question, on the same model, with the
same tools and the same cost ceiling, and differ **only in orchestration** — which is the
comparison the whole project is about:

```bash
python examples/web_research.py "Did the ECB cut rates in July 2026?" --agent all --trace
```

| Agent | Orchestration |
|---|---|
| `pipeline` | Fixed order: plan queries → *(search → take notes)* until sufficient → draft → critique. The plan decides what runs. |
| `freeform` | One prompt, the same two search tools, a budget of eight tool calls. The model decides what runs, in what order, how many times. |
| `plugin` | No search tool at all — OpenRouter's `web` plugin retrieves inside the model call. |

And the three-step one, for checking the plumbing in a few seconds and a fraction of a cent:

```bash
python examples/smoke_test.py "How many piano tuners are there in Chicago?" --trace
```

Every run returns an `AgentResult`: the answer, the final state, the full span tree, and the
cost ledger — one JSON object holding both what a voter needs to see and what the optimizer
needs to read.

```
trace 5b33cd140c51 agent='researcher-pipeline' 34.21s $0.19500 (6 calls)
✓ researcher-pipeline (agent) 34.21s
  ✓ plan_queries (step) 3.10s
    ✓ llm (llm) 3.10s $0.00310
  ✓ research (loop) 19.44s
    ✓ iteration 1 (iteration) 19.44s
      ✓ choose_query (step) 1.02s
      ✓ search (step) 0.83s
        ✓ search (tool) 0.83s $0.00400
      ✓ take_notes (step) 8.31s
  ✓ draft (step) 7.20s
  ✓ critique (step) 4.47s
```

Neither test needs a key or a network — both run against a fake OpenRouter:

```bash
python tests/test_end_to_end.py     # the runtime
python tests/test_examples.py       # the sample agents
```

See [`rsi_arena/README.md`](rsi_arena/README.md) for how to write an agent, add a step type, or
register a new API.

## Status

Early. The runtime in [`rsi_arena/`](rsi_arena/) runs, with sample agents and offline tests.
The [topics](topics/) are specified and the Kalshi data layer for the sports and market topics
is built. The arena — battles, votes, ratings, and the optimizer that rewrites losing harnesses
— is described here and not yet implemented.

## License

Apache 2.0 — see [LICENSE](LICENSE).
