"use client";

/**
 * One agent, one question, watched live.
 *
 * The debugging view: pick a harness, run it, and watch which primitives fire,
 * in what order, and what each one costs. The plan is shown beside the controls
 * because most of what you learn here is "the plan did not do what I assumed".
 */

import { useState } from "react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { AgentSelect, ModelSelect, NumberField, QuestionBox } from "@/components/controls";
import { HealthBanner } from "@/components/health-banner";
import { RunPanel } from "@/components/run-panel";
import { useCatalogue } from "@/lib/use-catalogue";
import { useRun } from "@/lib/use-run";

export default function PlaygroundPage() {
  const { agents, models, health, error } = useCatalogue();
  const { sides, running, fatal, start, stop } = useRun();

  // Only an explicit pick is stored; the effective value falls back to the
  // first runnable agent. Derived rather than written into state on load, so
  // the catalogue arriving does not trigger a second render pass.
  const [pickedAgent, setPickedAgent] = useState<string | null>(null);
  const [pickedModel, setPickedModel] = useState<string | null>(null);
  const [temperature, setTemperature] = useState(0.3);
  const [maxUsd, setMaxUsd] = useState(1.0);
  const [question, setQuestion] = useState("");

  const agentId =
    pickedAgent ?? agents.find((a) => a.missing_keys.length === 0)?.id ?? agents[0]?.id ?? "";
  const model = pickedModel ?? models[0]?.id ?? "";
  const selected = agents.find((a) => a.id === agentId);

  return (
    <div className="space-y-4">
      <HealthBanner health={health} error={error} />

      <div className="grid gap-4 lg:grid-cols-[380px_minmax(0,1fr)]">
        <div className="space-y-4">
          <Card>
            <CardHeader className="pb-3">
              <CardTitle className="text-base">Run an agent</CardTitle>
            </CardHeader>
            <CardContent className="space-y-4">
              <AgentSelect agents={agents} value={agentId} onChange={setPickedAgent} label="Agent" />
              <ModelSelect models={models} value={model} onChange={setPickedModel} />
              <div className="grid grid-cols-2 gap-3">
                <NumberField
                  label="Temperature"
                  value={temperature}
                  onChange={setTemperature}
                  step={0.1}
                  min={0}
                  max={2}
                />
                <NumberField
                  label="Budget (USD)"
                  value={maxUsd}
                  onChange={setMaxUsd}
                  step={0.25}
                  min={0.01}
                  max={20}
                  hint="Enforced: the run stops when crossed."
                />
              </div>
              <QuestionBox
                value={question}
                onChange={setQuestion}
                onSubmit={() =>
                  start("/api/run", {
                    agent: agentId,
                    question,
                    model,
                    temperature,
                    max_usd: maxUsd,
                  })
                }
                running={running}
                onStop={stop}
                disabled={!agentId || !!selected?.missing_keys.length}
                cta="Run"
              />
              {fatal && <p className="text-xs text-destructive">{fatal}</p>}
            </CardContent>
          </Card>

          {selected && (
            <Card>
              <CardHeader className="pb-3">
                <CardTitle className="text-base">{selected.name}</CardTitle>
              </CardHeader>
              <CardContent className="space-y-3 text-xs">
                <p className="text-muted-foreground">{selected.description}</p>
                <div>
                  <p className="mb-1 font-medium">Plan</p>
                  <pre className="overflow-auto rounded-md bg-muted p-2 font-mono text-[11px] leading-relaxed">
                    {selected.outline}
                  </pre>
                </div>
                <div>
                  <p className="mb-1 font-medium">Tools</p>
                  <p className="font-mono text-[11px] text-muted-foreground">
                    {selected.tools.join(", ") || "none"}
                  </p>
                </div>
                <div>
                  <p className="mb-1 font-medium">Context</p>
                  <p className="max-h-40 overflow-auto whitespace-pre-wrap text-[11px] text-muted-foreground">
                    {selected.context}
                  </p>
                </div>
              </CardContent>
            </Card>
          )}
        </div>

        <RunPanel side={sides.a} title="Output" />
      </div>
    </div>
  );
}
