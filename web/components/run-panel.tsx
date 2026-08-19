"use client";

/**
 * One agent's output: the answer as it streams, then its trace and ledger.
 *
 * In a blind battle the header shows "A" and no name — identity arrives only
 * after a vote, which is the whole reason the arena is worth anything.
 */

import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Separator } from "@/components/ui/separator";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { TraceTree } from "@/components/trace-tree";
import { type SideState } from "@/lib/use-run";
import { usd } from "@/lib/api";
import { cn } from "@/lib/utils";

export function RunPanel({
  side,
  title,
  blind,
  revealed,
}: {
  side: SideState;
  title: string;
  blind?: boolean;
  revealed?: string | null;
}) {
  // While tokens stream, `text` is the live answer; once the run ends,
  // `output` is authoritative (a structured step returns JSON, not tokens).
  const body = side.output || side.text;
  const name = revealed ?? (blind ? null : side.agent);

  return (
    <Card className="flex min-h-0 flex-col">
      <CardHeader className="flex-row items-center gap-2 space-y-0 pb-3">
        <CardTitle className="text-base">{title}</CardTitle>
        {name && (
          <Badge variant="secondary" className="font-mono text-[10px]">
            {name}
          </Badge>
        )}
        {side.status === "running" && (
          <Badge variant="outline" className="animate-pulse text-[10px]">
            running
          </Badge>
        )}
        {side.status === "error" && (
          <Badge variant="destructive" className="text-[10px]">
            failed
          </Badge>
        )}
        <div className="ml-auto flex items-center gap-3 text-xs tabular-nums text-muted-foreground">
          <span title="Spend so far on this side">{usd(side.totalUsd)}</span>
          <span title="Billed calls">{side.calls} calls</span>
          {side.summary && <span>{side.summary.duration_s.toFixed(1)}s</span>}
        </div>
      </CardHeader>
      <Separator />
      <CardContent className="flex min-h-0 flex-1 flex-col p-0">
        <Tabs defaultValue="answer" className="flex min-h-0 flex-1 flex-col">
          <TabsList className="mx-3 mt-3 w-fit">
            <TabsTrigger value="answer">Answer</TabsTrigger>
            <TabsTrigger value="trace">
              Trace{side.order.length > 0 && ` (${side.order.length})`}
            </TabsTrigger>
            <TabsTrigger value="cost">Cost</TabsTrigger>
          </TabsList>

          <TabsContent value="answer" className="min-h-0 flex-1 overflow-auto px-4 py-3">
            {side.error && (
              <p className="mb-3 rounded-md bg-destructive/10 p-3 text-xs text-destructive">
                {side.error}
              </p>
            )}
            {body ? (
              <div className="whitespace-pre-wrap text-sm leading-relaxed">
                {body}
                {side.status === "running" && (
                  <span className="ml-0.5 inline-block h-4 w-1.5 animate-pulse bg-foreground align-text-bottom" />
                )}
              </div>
            ) : (
              <p className="py-8 text-center text-xs text-muted-foreground">
                {side.status === "running"
                  ? "Working — the answer streams in once the final step starts."
                  : "No answer yet."}
              </p>
            )}
            {side.citations.length > 0 && (
              <div className="mt-4 border-t pt-3">
                <p className="mb-1 text-xs font-medium text-muted-foreground">Sources</p>
                <ul className="space-y-0.5">
                  {side.citations.map((c, i) => (
                    <li key={`${c.url}-${i}`} className="truncate text-xs">
                      <a
                        href={c.url}
                        target="_blank"
                        rel="noreferrer"
                        className="text-blue-600 hover:underline dark:text-blue-400"
                      >
                        {c.title || c.url}
                      </a>
                    </li>
                  ))}
                </ul>
              </div>
            )}
          </TabsContent>

          <TabsContent value="trace" className="min-h-0 flex-1 overflow-auto px-1 py-2">
            <TraceTree side={side} />
          </TabsContent>

          <TabsContent value="cost" className="min-h-0 flex-1 overflow-auto px-4 py-3">
            {side.summary ? (
              <dl className="space-y-1 text-xs">
                <Line label="Total" value={usd(side.summary.total_usd)} strong />
                <Line label="Billed calls" value={String(side.summary.calls)} />
                <Line label="Served from cache" value={String(side.summary.cached_calls)} />
                <Line label="Wall clock" value={`${side.summary.duration_s.toFixed(1)}s`} />
                <Separator className="my-2" />
                {Object.entries(side.summary.by_name).map(([name, amount]) => (
                  <Line key={name} label={name} value={usd(amount)} mono />
                ))}
                <Separator className="my-2" />
                {Object.entries(side.summary.usage).map(([name, count]) => (
                  <Line key={name} label={name.replace(/_/g, " ")} value={count.toLocaleString()} />
                ))}
              </dl>
            ) : (
              <p className="py-8 text-center text-xs text-muted-foreground">
                The ledger is written when the run finishes.
              </p>
            )}
          </TabsContent>
        </Tabs>
      </CardContent>
    </Card>
  );
}

function Line({
  label,
  value,
  strong,
  mono,
}: {
  label: string;
  value: string;
  strong?: boolean;
  mono?: boolean;
}) {
  return (
    <div className="flex justify-between gap-4">
      <dt className={cn("truncate text-muted-foreground", mono && "font-mono text-[11px]")}>
        {label}
      </dt>
      <dd className={cn("shrink-0 tabular-nums", strong && "font-semibold")}>{value}</dd>
    </div>
  );
}
