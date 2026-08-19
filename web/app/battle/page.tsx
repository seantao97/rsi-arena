"use client";

/**
 * Two agents, one question, blind and side by side.
 *
 * The arena proper. Both run concurrently against one shared backend client,
 * so they share a rate limiter and a cache and neither can outspend the other
 * on retries. Sides are shuffled server-side and names withheld until the vote
 * lands, because otherwise the thing being measured is brand recognition.
 */

import { useState } from "react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import { AgentSelect, ModelSelect, NumberField, QuestionBox } from "@/components/controls";
import { HealthBanner } from "@/components/health-banner";
import { RunPanel } from "@/components/run-panel";
import { VoteBar } from "@/components/vote-bar";
import { useCatalogue } from "@/lib/use-catalogue";
import { useRun } from "@/lib/use-run";
import { type VoteResult } from "@/lib/api";

export default function BattlePage() {
  const { agents, models, health, error, runnable } = useCatalogue();
  const { sides, running, battleId, fatal, start, stop } = useRun();

  // Defaults are derived from the catalogue rather than written into state
  // when it loads; only an explicit pick is stored.
  const [pickedA, setPickedA] = useState<string | null>(null);
  const [pickedB, setPickedB] = useState<string | null>(null);
  const [pickedModel, setPickedModel] = useState<string | null>(null);
  const [maxUsd, setMaxUsd] = useState(1.0);
  const [question, setQuestion] = useState("");
  const [reveal, setReveal] = useState<VoteResult["reveal"] | null>(null);

  const agentA = pickedA ?? runnable[0]?.id ?? "";
  const agentB = pickedB ?? runnable[1]?.id ?? "";
  const model = pickedModel ?? models[0]?.id ?? "";

  const bothFinished =
    (sides.a.status === "done" || sides.a.status === "error") &&
    (sides.b.status === "done" || sides.b.status === "error");

  return (
    <div className="space-y-4">
      <HealthBanner health={health} error={error} />

      <Card>
        <CardHeader className="pb-3">
          <CardTitle className="text-base">Battle</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="grid gap-3 md:grid-cols-4">
            <AgentSelect agents={agents} value={agentA} onChange={setPickedA} label="Agent one" />
            <AgentSelect agents={agents} value={agentB} onChange={setPickedB} label="Agent two" />
            <ModelSelect models={models} value={model} onChange={setPickedModel} />
            <NumberField
              label="Budget each (USD)"
              value={maxUsd}
              onChange={setMaxUsd}
              step={0.25}
              min={0.01}
              max={20}
              hint="Both sides get the same ceiling."
            />
          </div>
          <QuestionBox
            value={question}
            onChange={setQuestion}
            onSubmit={() => {
              setReveal(null);
              start("/api/battle", {
                agent_a: agentA,
                agent_b: agentB,
                question,
                model,
                max_usd: maxUsd,
                blind: true,
              });
            }}
            running={running}
            onStop={stop}
            disabled={!agentA || !agentB || agentA === agentB}
            cta="Run both"
          />
          {agentA === agentB && agentA !== "" && (
            <p className="text-xs text-muted-foreground">
              Pick two different agents — the same harness twice measures sampling noise.
            </p>
          )}
          {fatal && <p className="text-xs text-destructive">{fatal}</p>}
          <div>
            <Label className="text-xs text-muted-foreground">
              Sides are shuffled and names hidden until you vote.
            </Label>
          </div>
        </CardContent>
      </Card>

      <div className="grid gap-4 lg:grid-cols-2">
        <RunPanel side={sides.a} title="A" blind revealed={reveal?.a ?? null} />
        <RunPanel side={sides.b} title="B" blind revealed={reveal?.b ?? null} />
      </div>

      <VoteBar
        battleId={battleId}
        ready={bothFinished}
        onVoted={(result) => setReveal(result.reveal)}
      />
    </div>
  );
}
