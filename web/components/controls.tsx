"use client";

/**
 * Shared controls: agent pickers, model, temperature, budget, question.
 *
 * The budget field is not decoration. `max_usd` is enforced by the backend —
 * a run that crosses it stops and returns its partial trace — so it is the one
 * knob that decides what a careless question can cost.
 */

import { Loader2, Play, Square } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import { type AgentInfo, type ModelInfo } from "@/lib/api";

export function AgentSelect({
  agents,
  value,
  onChange,
  label,
}: {
  agents: AgentInfo[];
  value: string;
  onChange: (value: string) => void;
  label: string;
}) {
  return (
    <div className="space-y-1.5">
      <Label className="text-xs">{label}</Label>
      <Select value={value} onValueChange={(next) => next && onChange(next)}>
        <SelectTrigger className="w-full">
          <SelectValue placeholder="Pick an agent" />
        </SelectTrigger>
        <SelectContent>
          {agents.map((agent) => (
            <SelectItem
              key={agent.id}
              value={agent.id}
              disabled={agent.missing_keys.length > 0}
            >
              <span className="font-medium">{agent.id}</span>
              <span className="ml-2 text-xs text-muted-foreground">
                {agent.missing_keys.length > 0
                  ? `needs ${agent.missing_keys.join(", ")}`
                  : agent.description}
              </span>
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
    </div>
  );
}

export function ModelSelect({
  models,
  value,
  onChange,
}: {
  models: ModelInfo[];
  value: string;
  onChange: (value: string) => void;
}) {
  return (
    <div className="space-y-1.5">
      <Label className="text-xs">Model</Label>
      <Select value={value} onValueChange={(next) => next && onChange(next)}>
        <SelectTrigger className="w-full">
          <SelectValue placeholder="Pick a model" />
        </SelectTrigger>
        <SelectContent className="max-h-72">
          {models.slice(0, 60).map((model) => (
            <SelectItem key={model.id} value={model.id}>
              <span className="font-mono text-xs">{model.id}</span>
              {model.suggested && (
                <span className="ml-2 text-[10px] text-muted-foreground">suggested</span>
              )}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
    </div>
  );
}

export function NumberField({
  label,
  value,
  onChange,
  step,
  min,
  max,
  hint,
}: {
  label: string;
  value: number;
  onChange: (value: number) => void;
  step: number;
  min: number;
  max: number;
  hint?: string;
}) {
  return (
    <div className="space-y-1.5">
      <Label className="text-xs">{label}</Label>
      <Input
        type="number"
        value={value}
        step={step}
        min={min}
        max={max}
        onChange={(event) => onChange(Number(event.target.value))}
      />
      {hint && <p className="text-[10px] text-muted-foreground">{hint}</p>}
    </div>
  );
}

export function QuestionBox({
  value,
  onChange,
  onSubmit,
  running,
  onStop,
  disabled,
  cta,
}: {
  value: string;
  onChange: (value: string) => void;
  onSubmit: () => void;
  running: boolean;
  onStop: () => void;
  disabled?: boolean;
  cta: string;
}) {
  return (
    <div className="space-y-2">
      <Label className="text-xs">Question</Label>
      <Textarea
        value={value}
        onChange={(event) => onChange(event.target.value)}
        onKeyDown={(event) => {
          // Enter submits; Shift+Enter is a newline. A question is usually one
          // line and reaching for the mouse for every run gets old fast.
          if (event.key === "Enter" && !event.shiftKey) {
            event.preventDefault();
            if (!running && !disabled) onSubmit();
          }
        }}
        rows={3}
        placeholder="Did the ECB cut rates in July 2026?"
        className="resize-none"
      />
      <div className="flex gap-2">
        <Button onClick={onSubmit} disabled={running || disabled || !value.trim()}>
          {running ? (
            <Loader2 className="size-4 animate-spin" />
          ) : (
            <Play className="size-4" />
          )}
          {cta}
        </Button>
        {running && (
          <Button variant="outline" onClick={onStop}>
            <Square className="size-4" />
            Stop
          </Button>
        )}
      </div>
    </div>
  );
}
