import { Badge } from "@/components/ui/badge"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Progress } from "@/components/ui/progress"
import type { AgentDecision } from "@/api"

export function AgentPanel({ agent }: { agent: AgentDecision }) {
  const confidencePct = Math.round(agent.confidence * 100)
  // Items queued by the conflict policy are links that were *removed*: a
  // plain "Match" badge would read as the opposite of the current state.
  const conflictCut = agent.decided_by.startsWith("conflict-policy")

  return (
    <Card data-testid="agent-panel" size="sm">
      <CardHeader className="flex flex-row items-center justify-between">
        <CardTitle>{conflictCut ? "Conflict policy" : "Agent assessment"}</CardTitle>
        <Badge
          data-testid="agent-verdict"
          className={
            conflictCut
              ? "border-violet-200 bg-violet-100 text-violet-800"
              : agent.match
                ? "border-emerald-200 bg-emerald-100 text-emerald-800"
                : "border-amber-200 bg-amber-100 text-amber-800"
          }
        >
          {conflictCut ? "Link cut — confirm split?" : agent.match ? "Match" : "No match"}
        </Badge>
      </CardHeader>
      <CardContent className="flex flex-col gap-3">
        <div className="flex items-center gap-3" data-testid="agent-confidence">
          <span className="w-20 shrink-0 text-xs text-muted-foreground">Confidence</span>
          <Progress value={confidencePct} className="h-1.5" />
          <span className="w-12 shrink-0 text-right text-sm font-semibold tabular-nums">
            {agent.confidence.toFixed(2)}
          </span>
        </div>

        {agent.evidence.length > 0 && (
          <div className="flex items-start gap-3">
            <span className="w-20 shrink-0 pt-0.5 text-xs text-muted-foreground">
              Evidence
            </span>
            <div className="flex flex-wrap gap-1.5">
              {agent.evidence.map((ev, i) => (
                <Badge
                  key={`${ev}-${i}`}
                  variant="secondary"
                  data-testid="evidence-chip"
                  className="h-auto max-w-full font-normal whitespace-normal"
                >
                  {ev}
                </Badge>
              ))}
            </div>
          </div>
        )}

        <div className="flex items-start gap-3">
          <span className="w-20 shrink-0 pt-0.5 text-xs text-muted-foreground">Reason</span>
          <p className="text-sm leading-relaxed" data-testid="agent-reason">
            {agent.reason}
          </p>
        </div>

        <p className="text-xs text-muted-foreground">decided by {agent.decided_by}</p>
      </CardContent>
    </Card>
  )
}
