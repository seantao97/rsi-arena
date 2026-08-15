# RSI Arena

A Chatbot-Arena-style leaderboard for **agents** instead of models — plus a background loop that uses the arena's own votes to rewrite the agents that lose.

Chatbot Arena answers "which model is better?" But in practice nobody ships a bare model. They ship a *harness*: a model wired to a set of tools, with a prompt that says how and when to use them. Two harnesses over the same model can be far apart in quality. RSI Arena measures that whole package, and then closes the loop — the winners get mutated into the next generation of contenders.

RSI stands for *recursive self-improvement*: the agents in the arena are optimized by LLMs, using preference data produced by humans judging those same agents.

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

## Status

Early. This README describes the intended design; the implementation is not built yet.

## License

Apache 2.0 — see [LICENSE](LICENSE).
