export function EmptyState() {
  return (
    <div
      data-testid="empty-state"
      className="flex flex-col items-center justify-center gap-3 rounded-xl border border-dashed border-border bg-card px-6 py-16 text-center"
    >
      <svg
        aria-hidden
        viewBox="0 0 48 48"
        className="size-14 text-emerald-500"
        fill="none"
        stroke="currentColor"
        strokeWidth="2.5"
        strokeLinecap="round"
        strokeLinejoin="round"
      >
        <circle cx="24" cy="24" r="20" className="opacity-30" />
        <path d="M15 24.5l6.5 6.5L33 18" />
      </svg>
      <h2 className="text-lg font-semibold">Queue clear</h2>
      <p className="max-w-sm text-sm text-muted-foreground">
        Every pending pair has been reviewed. New pairs will appear here after the next
        pipeline run.
      </p>
    </div>
  )
}
