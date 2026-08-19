# Web app

Next.js + Tailwind + shadcn on **:8050**. Talks to the FastAPI backend on :3600.

```bash
npm install
npm run dev            # http://localhost:8050
```

Point it somewhere else with `NEXT_PUBLIC_API_BASE=http://host:port` in `.env.local`.
The backend must be running or every page shows a banner saying so.

## Two tabs

**Playground** — one agent, one question, watched live. Pick the harness, the model, a
temperature and a budget; the plan, tools and orchestration prompt are shown beside the
controls, because most of what you learn here is that the plan did not do what you assumed.

**Battle** — two agents on the same question, same model, same ceiling, run concurrently and
shown blind. Vote when both finish; the names are revealed only then.

Each side has three views: the **answer** as it streams, the **trace** filling in live, and the
**cost** ledger once the run ends.

## The trace

Spans arrive flat with a `parent_id` and the tree is rebuilt on each render. Open rows pulse;
closed ones show duration and, where there is one, price. Per-row cost is the point — a harness
that looks thorough and a harness that is expensive produce the same trace until you can see
what each step charged.

## Files

| | |
|---|---|
| `lib/api.ts` | Types and the SSE reader. POST + `fetch`, since `EventSource` is GET-only |
| `lib/use-run.ts` | Reduces the event stream into per-side state. One hook serves both tabs |
| `lib/use-catalogue.ts` | Loads agents, models and health once |
| `components/trace-tree.tsx` | The live span tree |
| `components/run-panel.tsx` | One side: answer, trace, ledger |
| `components/controls.tsx` | Agent, model, temperature, budget, question |
| `components/vote-bar.tsx` | Winner or tie, one reason, then the reveal |

## Notes

`npm run build` typechecks and lints as part of the build; both are clean.

Streaming needs an unbuffered path. The backend sets `X-Accel-Buffering: no` for the benefit of
anything proxying in front of it — without that, nginx holds `text/event-stream` and the live
trace arrives as one burst at the end.
