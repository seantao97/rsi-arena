"use client";

/**
 * Says which keys the backend actually has, and whether it is up at all.
 *
 * Worth the space: the two failure modes here — backend not running, key not
 * exported — look identical from inside the page, and both waste a minute
 * before the error appears.
 */

import { AlertCircle, CircleCheck } from "lucide-react";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { API_BASE, type Health } from "@/lib/api";

export function HealthBanner({ health, error }: { health: Health | null; error: string | null }) {
  if (error) {
    return (
      <Alert variant="destructive">
        <AlertCircle className="size-4" />
        <AlertDescription>
          Cannot reach the backend at <code className="font-mono">{API_BASE}</code>. Start it with{" "}
          <code className="font-mono">python -m server</code> from the repo root.
        </AlertDescription>
      </Alert>
    );
  }
  if (!health) return null;

  const missing = Object.entries(health.keys)
    .filter(([, present]) => !present)
    .map(([name]) => name);

  if (missing.length === 0) {
    return (
      <p className="flex items-center gap-1.5 text-xs text-muted-foreground">
        <CircleCheck className="size-3.5 text-emerald-600 dark:text-emerald-400" />
        Backend up, all keys present.
      </p>
    );
  }

  return (
    <Alert>
      <AlertCircle className="size-4" />
      <AlertDescription>
        Missing <code className="font-mono">{missing.join(", ")}</code>. Export it and restart the
        backend — agents needing it are greyed out.
        {missing.length === 1 && missing[0] === "SEARCHAPI_API_KEY" && (
          <> The <code className="font-mono">plugin</code> agent still runs; it searches through OpenRouter.</>
        )}
      </AlertDescription>
    </Alert>
  );
}
