import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { Separator } from "@/components/ui/separator"
import type { Role, Stats } from "@/api"

interface StatChipProps {
  label: string
  value: number | undefined
  testId: string
  tone?: "neutral" | "positive" | "negative"
}

function StatChip({ label, value, testId, tone = "neutral" }: StatChipProps) {
  const toneClass =
    tone === "positive"
      ? "text-emerald-700"
      : tone === "negative"
        ? "text-red-700"
        : "text-foreground"

  return (
    <div className="flex items-center gap-1.5 rounded-md border border-border bg-card px-2.5 py-1">
      <span className="text-xs text-muted-foreground">{label}</span>
      <span className={`text-sm font-semibold tabular-nums ${toneClass}`} data-testid={testId}>
        {value ?? "–"}
      </span>
    </div>
  )
}

interface StatsHeaderProps {
  role: Role
  onRoleChange: (role: Role) => void
  stats: Stats | undefined
}

export function StatsHeader({ role, onRoleChange, stats }: StatsHeaderProps) {
  const precision = stats?.metrics?.precision
  const precisionLabel =
    precision === null || precision === undefined ? "n/a" : `${(precision * 100).toFixed(1)}%`
  const pendingIngestion = stats?.decisions_pending_ingestion ?? 0

  return (
    <header className="border-b border-border bg-card">
      <div className="mx-auto flex max-w-5xl flex-wrap items-center gap-x-4 gap-y-2 px-4 py-3">
        <h1 className="text-base font-semibold tracking-tight">
          Review Queue — Agentic CDP
        </h1>

        <Separator orientation="vertical" className="hidden h-5! sm:block" />

        <div className="flex flex-wrap items-center gap-2">
          <StatChip label="Pending" value={stats?.pending} testId="stats-pending" />
          <StatChip
            label="Approved"
            value={stats?.approved}
            testId="stats-approved"
            tone="positive"
          />
          <StatChip
            label="Rejected"
            value={stats?.rejected}
            testId="stats-rejected"
            tone="negative"
          />
          <div className="flex items-center gap-1.5 rounded-md border border-border bg-card px-2.5 py-1">
            <span className="text-xs text-muted-foreground">Precision</span>
            <span
              className="text-sm font-semibold tabular-nums"
              data-testid="stats-precision"
            >
              {precisionLabel}
            </span>
          </div>
          {pendingIngestion > 0 && (
            <div
              className="rounded-md border border-amber-200 bg-amber-50 px-2.5 py-1 text-xs text-amber-800"
              data-testid="pending-ingestion-chip"
            >
              {pendingIngestion} decision{pendingIngestion === 1 ? "" : "s"} awaiting
              pipeline re-run
            </div>
          )}
        </div>

        <div className="ml-auto flex items-center gap-2">
          <span className="text-xs text-muted-foreground">Role</span>
          <Select value={role} onValueChange={(value) => onRoleChange(value as Role)}>
            <SelectTrigger data-testid="role-select" className="min-w-28">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="steward">Steward</SelectItem>
              <SelectItem value="analyst">Analyst</SelectItem>
            </SelectContent>
          </Select>
        </div>
      </div>
    </header>
  )
}
