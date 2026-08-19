"use client";

/**
 * The span tree, drawn as it fills in.
 *
 * Spans arrive flat with a `parent_id`, so the tree is rebuilt on each render
 * from arrival order. Rows that are still open pulse; rows that closed show
 * their duration and, if they cost anything, what they cost. Showing money per
 * row is the point — a harness that looks thorough and a harness that is
 * expensive are the same trace until you can see the price of each step.
 */

import { useMemo, useState } from "react";
import { ChevronRight, Circle, CircleAlert, CircleCheck, MinusCircle } from "lucide-react";
import { type SideState } from "@/lib/use-run";
import { type SpanPayload, usd } from "@/lib/api";
import { cn } from "@/lib/utils";

const KIND_STYLE: Record<SpanPayload["kind"], string> = {
  agent: "bg-foreground/10 text-foreground",
  step: "bg-blue-500/10 text-blue-600 dark:text-blue-400",
  loop: "bg-violet-500/10 text-violet-600 dark:text-violet-400",
  iteration: "bg-violet-500/5 text-violet-600/80 dark:text-violet-400/80",
  llm: "bg-emerald-500/10 text-emerald-600 dark:text-emerald-400",
  tool: "bg-amber-500/10 text-amber-600 dark:text-amber-400",
  api: "bg-amber-500/10 text-amber-600 dark:text-amber-400",
};

type Node = { span: SpanPayload; children: Node[] };

function build(side: SideState): Node[] {
  const nodes = new Map<string, Node>();
  side.order.forEach((id) => {
    const span = side.spans[id];
    if (span) nodes.set(id, { span, children: [] });
  });
  const roots: Node[] = [];
  nodes.forEach((node) => {
    const parent = node.span.parent_id ? nodes.get(node.span.parent_id) : undefined;
    if (parent) parent.children.push(node);
    else roots.push(node);
  });
  return roots;
}

function StatusIcon({ status }: { status: SpanPayload["status"] }) {
  if (status === "running")
    return <Circle className="size-3.5 shrink-0 animate-pulse text-muted-foreground" />;
  if (status === "error") return <CircleAlert className="size-3.5 shrink-0 text-destructive" />;
  if (status === "skipped")
    return <MinusCircle className="size-3.5 shrink-0 text-muted-foreground" />;
  return <CircleCheck className="size-3.5 shrink-0 text-emerald-600 dark:text-emerald-400" />;
}

function Row({ node, depth }: { node: Node; depth: number }) {
  const [open, setOpen] = useState(true);
  const { span } = node;
  const detail = span.output ?? span.error ?? null;
  const hasChildren = node.children.length > 0;

  return (
    <div>
      <div
        className={cn(
          "group flex items-center gap-2 rounded-md px-2 py-1 text-xs hover:bg-muted/60",
          span.status === "error" && "bg-destructive/5",
        )}
        style={{ paddingLeft: `${depth * 14 + 8}px` }}
      >
        {hasChildren ? (
          <button
            onClick={() => setOpen(!open)}
            className="-ml-1 shrink-0 text-muted-foreground"
            aria-label={open ? "Collapse" : "Expand"}
          >
            <ChevronRight className={cn("size-3 transition-transform", open && "rotate-90")} />
          </button>
        ) : (
          <span className="w-2 shrink-0" />
        )}
        <StatusIcon status={span.status} />
        <span className="truncate font-medium">{span.name}</span>
        <span className={cn("rounded px-1.5 py-0.5 text-[10px] font-medium", KIND_STYLE[span.kind])}>
          {span.kind}
        </span>
        {span.cached && (
          <span className="rounded bg-muted px-1.5 py-0.5 text-[10px] text-muted-foreground">
            cached
          </span>
        )}
        <span className="ml-auto shrink-0 tabular-nums text-muted-foreground">
          {span.status === "running" ? "…" : `${span.duration_s.toFixed(2)}s`}
        </span>
        {span.cost_usd > 0 && (
          <span className="w-16 shrink-0 text-right tabular-nums text-muted-foreground">
            {usd(span.cost_usd)}
          </span>
        )}
      </div>
      {detail && (
        <p
          className="truncate pr-2 text-[11px] text-muted-foreground/80"
          style={{ paddingLeft: `${depth * 14 + 40}px` }}
          title={detail}
        >
          {detail}
        </p>
      )}
      {open &&
        node.children.map((child) => (
          <Row key={child.span.id} node={child} depth={depth + 1} />
        ))}
    </div>
  );
}

export function TraceTree({ side }: { side: SideState }) {
  const roots = useMemo(() => build(side), [side]);
  if (roots.length === 0) {
    return (
      <p className="px-2 py-6 text-center text-xs text-muted-foreground">
        The trace appears here as the agent runs.
      </p>
    );
  }
  return (
    <div className="font-mono">
      {roots.map((node) => (
        <Row key={node.span.id} node={node} depth={0} />
      ))}
    </div>
  );
}
