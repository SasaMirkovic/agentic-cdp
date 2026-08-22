import { useCallback, useEffect, useRef, useState, type ReactNode } from "react"
import { Button } from "@/components/ui/button"
import { ActionBar } from "@/components/ActionBar"
import { AgentPanel } from "@/components/AgentPanel"
import { EmptyState } from "@/components/EmptyState"
import { PairCard } from "@/components/PairCard"
import { StatsHeader } from "@/components/StatsHeader"
import { useDecisionMutation, useNextPair, useStats } from "@/hooks"
import type { Decision, Role } from "@/api"

const SHORTCUTS: Record<string, Decision> = {
  a: "approve",
  r: "reject",
  s: "skip",
}

/**
 * True when the event originated in a form control where letter keys carry
 * meaning (text inputs, the role combobox, its listbox). Plain action buttons
 * are excluded on purpose so shortcuts still work right after a mouse click.
 */
function isFormControlTarget(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false
  return (
    target.closest(
      "input, textarea, select, [contenteditable='true'], [role='combobox'], [role='listbox'], [role='option']"
    ) !== null
  )
}

function CenteredNotice({ children }: { children: ReactNode }) {
  return (
    <div className="flex flex-col items-center justify-center gap-3 rounded-xl border border-border bg-card px-6 py-16 text-center text-sm text-muted-foreground">
      {children}
    </div>
  )
}

// After a new pair renders, ignore decisions for a short window so a second
// click or key repeat aimed at the previous pair cannot land on this one.
const SETTLE_MS = 350

export default function App() {
  // Fail closed like the API: PII is masked until the reviewer elevates to steward.
  const [role, setRole] = useState<Role>("analyst")
  const stats = useStats(role)
  const nextPair = useNextPair(role)
  const decision = useDecisionMutation(role)

  const item = nextPair.data ?? null
  const busy = decision.isPending || nextPair.isFetching

  // Synchronous in-flight guard: mutation state updates are batched, so two
  // clicks in the same tick would both pass an `isPending` check.
  const inFlightRef = useRef(false)
  const [settling, setSettling] = useState(false)
  const shownItemRef = useRef<number | null>(null)
  useEffect(() => {
    if (!item || item.id === shownItemRef.current) return
    shownItemRef.current = item.id
    setSettling(true)
    const timer = window.setTimeout(() => setSettling(false), SETTLE_MS)
    return () => window.clearTimeout(timer)
  }, [item])

  const decide = useCallback(
    (choice: Decision) => {
      if (!item || inFlightRef.current || settling) return
      inFlightRef.current = true
      decision.mutate(
        { id: item.id, decision: choice },
        { onSettled: () => { inFlightRef.current = false } }
      )
    },
    [item, decision, settling]
  )

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.metaKey || event.ctrlKey || event.altKey) return
      if (isFormControlTarget(event.target)) return
      const mapped = SHORTCUTS[event.key.toLowerCase()]
      if (!mapped) return
      event.preventDefault()
      decide(mapped)
    }
    window.addEventListener("keydown", onKeyDown)
    return () => window.removeEventListener("keydown", onKeyDown)
  }, [decide])

  return (
    <div className="flex min-h-screen flex-col bg-muted/40">
      <StatsHeader role={role} onRoleChange={setRole} stats={stats.data} />

      <main className="mx-auto flex w-full max-w-5xl flex-1 flex-col gap-4 px-4 py-5">
        {nextPair.isPending ? (
          <CenteredNotice>Loading review queue…</CenteredNotice>
        ) : nextPair.isError ? (
          <CenteredNotice>
            <p className="text-destructive">
              Could not load the review queue: {nextPair.error.message}
            </p>
            <Button variant="outline" onClick={() => void nextPair.refetch()}>
              Retry
            </Button>
          </CenteredNotice>
        ) : item === null ? (
          <EmptyState />
        ) : (
          <>
            <PairCard item={item} />
            <AgentPanel agent={item.agent} />
            <ActionBar onDecision={decide} disabled={busy || settling} />
          </>
        )}

        {stats.isError && (
          <p className="text-center text-xs text-destructive">
            Stats unavailable: {stats.error.message}
          </p>
        )}
      </main>
    </div>
  )
}
