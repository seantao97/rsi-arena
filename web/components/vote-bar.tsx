"use client";

/**
 * The vote — the only supervision signal the arena has.
 *
 * Winner or tie, then one tap for why. The reasons are the ones the topic docs
 * ask for, kept to four because a long list gets answered at random. Identity
 * is revealed only after the vote lands, so a preference for a familiar name
 * cannot be the thing being measured.
 */

import { useState } from "react";
import { Trophy } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { postVote, type VoteResult } from "@/lib/api";
import { cn } from "@/lib/utils";

const REASONS = ["evidence", "reasoning", "risk framing", "counter-case"];

const CHOICES = [
  { key: "a", label: "A is better" },
  { key: "b", label: "B is better" },
  { key: "tie", label: "Tie" },
  { key: "both_bad", label: "Both are bad" },
] as const;

export function VoteBar({
  battleId,
  ready,
  onVoted,
}: {
  battleId: string | null;
  ready: boolean;
  onVoted: (result: VoteResult) => void;
}) {
  const [reason, setReason] = useState<string>("");
  const [result, setResult] = useState<VoteResult | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function vote(winner: (typeof CHOICES)[number]["key"]) {
    if (!battleId) return;
    setBusy(true);
    setError(null);
    try {
      const voted = await postVote({ battle_id: battleId, winner, reason });
      setResult(voted);
      onVoted(voted);
    } catch (caught) {
      setError((caught as Error).message);
    } finally {
      setBusy(false);
    }
  }

  if (result) {
    return (
      <Card>
        <CardContent className="flex flex-wrap items-center gap-3 py-4 text-sm">
          <Trophy className="size-4 text-amber-500" />
          <span className="text-muted-foreground">Recorded.</span>
          <span>
            A was <Badge variant="secondary">{result.reveal.a}</Badge>
          </span>
          <span>
            B was <Badge variant="secondary">{result.reveal.b}</Badge>
          </span>
          <div className="ml-auto flex flex-wrap gap-2 text-xs text-muted-foreground">
            {result.leaderboard.map((row) => (
              <span key={row.agent} className="tabular-nums">
                {row.agent} {row.wins}W–{row.losses}L–{row.ties}T
              </span>
            ))}
          </div>
        </CardContent>
      </Card>
    );
  }

  return (
    <Card className={cn(!ready && "opacity-50")}>
      <CardContent className="space-y-3 py-4">
        <div className="flex flex-wrap items-center gap-2">
          <span className="mr-1 text-sm font-medium">
            Which would you rather have read before deciding?
          </span>
          {CHOICES.map((choice) => (
            <Button
              key={choice.key}
              size="sm"
              variant={choice.key === "tie" || choice.key === "both_bad" ? "outline" : "default"}
              disabled={!ready || busy || !battleId}
              onClick={() => vote(choice.key)}
            >
              {choice.label}
            </Button>
          ))}
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-xs text-muted-foreground">Why (optional):</span>
          {REASONS.map((option) => (
            <button
              key={option}
              type="button"
              disabled={!ready}
              onClick={() => setReason(reason === option ? "" : option)}
              className={cn(
                "rounded-full border px-2.5 py-0.5 text-xs transition-colors",
                reason === option
                  ? "border-foreground bg-foreground text-background"
                  : "text-muted-foreground hover:bg-muted",
              )}
            >
              {option}
            </button>
          ))}
        </div>
        {error && <p className="text-xs text-destructive">{error}</p>}
        {!ready && (
          <p className="text-xs text-muted-foreground">
            Voting opens when both sides have finished.
          </p>
        )}
      </CardContent>
    </Card>
  );
}
