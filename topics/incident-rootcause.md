# Incident root cause

Given telemetry from a production incident, name the root cause.

Running example: **checkout p99 went from 180ms to 4.2s at 14:03 UTC and recovered at 14:31.**

## Scope

Logs, metrics, traces, deploys and config. **Source code review is out of scope** — an agent may see
what changed and when, not audit the diff. Code analysis is a separate topic and this one deliberately
leaves room for it.

## Objective

> Given telemetry from an incident, identify the root cause and the evidence chain that establishes it.

**Asked of the reader** — *"Which of these would you rather have had during the incident?"* Winner or tie,
then one tap for why: cause, evidence, timeline, or fix.

## Scoring

| | |
|---|---|
| Source | Synthetic incidents replayed from a fault-injection harness |
| Decision time | Unlimited within the answer budget; wall-clock recorded |
| Scored by | **Root-cause accuracy against the injected fault**, time to answer, and false-lead count |

The injected fault is known exactly, so this topic has ground truth that costs nothing to produce and
scales without limit. Real incidents can be added later for realism, but the synthetic corpus is what
makes the leaderboard trustworthy.

## Answer contract

| | |
|---|---|
| **Root cause** | One cause, stated as a mechanism. *"Connection pool exhaustion in `payments-svc`."* Not "database issues." |
| **Evidence chain** | The signals that establish it, in order, each with service, metric and timestamp. |
| **Trigger** | What started it, and why then. A deploy, a traffic shift, a config change, a dependency. |
| **Timeline** | Trigger → first symptom → saturation → recovery, with times. |
| **Ruled out** | At least one plausible alternative and the signal that eliminates it. |
| **Fix** | The minimal change that prevents recurrence. |

## Primitives

| | |
|---|---|
| `topology() → graph` | Service dependency graph. Which service calls which, with normal rates. |
| `metric_series(name, labels, window) → series` | Any metric, any window. |
| `query_logs(service, window, filter) → lines` | Structured log search. |
| `trace_sample(criteria) → traces` | Distributed traces matching a latency or error criterion. |
| `deploy_history(window) → events` | Deploys, rollbacks, feature-flag flips, with times and diffs of *what* shipped, not the code itself. |
| `diff_config(service, t1, t2) → changes` | Config and environment deltas across the incident window. |
| `correlate(series_a, series_b, lag) → score` | Cross-correlation with lag. Finds the upstream signal that moved first. |
| `anomaly_scan(window, scope) → candidates` | Which of a few thousand series moved abnormally. The triage primitive — without it the agent is guessing where to look. |
| `dependency_health(service, window) → status` | External dependencies: databases, queues, third-party APIs. |
| `saturation(resource, window) → utilisation` | Pools, threads, connections, disk, file descriptors. |
| `alert_history(window) → alerts` | What fired, and what was already firing before. |
| Shared | `compute` · `run_code` · `estimate` · `recall` · `remember` · `counter` · `cite` · `draft` · `critique` |

## Runtime additions

| | |
|---|---|
| `TelemetryStore` | Replayable incident snapshots — metrics, logs, traces, deploys, config. |
| `FaultHarness` | Injects the fault and holds the ground truth. Not readable by agents. |

## Notes

**The harness is the skill.** Which service to look at, in what order, when to stop — nothing here is
solved by a better model in isolation. That makes it the strongest test of orchestration on the roster.

**Time to answer is scored** because a correct root cause found in forty minutes is worth less than a
good hypothesis in four.
