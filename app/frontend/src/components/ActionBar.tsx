import { Button } from "@/components/ui/button"
import type { Decision } from "@/api"

function Shortcut({ label }: { label: string }) {
  return (
    <kbd className="rounded border border-current/25 px-1 font-mono text-[10px] leading-4 opacity-70">
      {label}
    </kbd>
  )
}

interface ActionBarProps {
  onDecision: (decision: Decision) => void
  disabled: boolean
}

export function ActionBar({ onDecision, disabled }: ActionBarProps) {
  return (
    <div className="flex flex-wrap items-center justify-center gap-3">
      <Button
        data-testid="btn-approve"
        disabled={disabled}
        onClick={() => onDecision("approve")}
        className="min-w-32 bg-emerald-600 text-white hover:bg-emerald-600/85"
      >
        Approve <Shortcut label="A" />
      </Button>
      <Button
        data-testid="btn-reject"
        variant="destructive"
        disabled={disabled}
        onClick={() => onDecision("reject")}
        className="min-w-32"
      >
        Reject <Shortcut label="R" />
      </Button>
      <Button
        data-testid="btn-skip"
        variant="outline"
        disabled={disabled}
        onClick={() => onDecision("skip")}
        className="min-w-32"
      >
        Skip <Shortcut label="S" />
      </Button>
    </div>
  )
}
