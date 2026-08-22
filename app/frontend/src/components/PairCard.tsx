import { Badge } from "@/components/ui/badge"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import type { CompanyRecord, QueueItem } from "@/api"

type FieldKey =
  | "company_name"
  | "domain"
  | "address"
  | "phone"
  | "industry"
  | "employees"
  | "revenue"
  | "source_system"

interface FieldSpec {
  key: FieldKey
  label: string
  /** Raw value shown in the cell. */
  display: (r: CompanyRecord) => string | number | null
  /** Value used for the agreement comparison (normalized where available). */
  compare: (r: CompanyRecord) => string | number | null
}

const FIELD_SPECS: FieldSpec[] = [
  {
    key: "company_name",
    label: "Company name",
    display: (r) => r.company_name,
    compare: (r) => r.normalized.name ?? r.company_name,
  },
  {
    key: "domain",
    label: "Domain",
    display: (r) => r.domain,
    compare: (r) => r.normalized.domain ?? r.domain,
  },
  {
    key: "address",
    label: "Address",
    display: (r) => r.address,
    compare: (r) => r.normalized.address ?? r.address,
  },
  {
    key: "phone",
    label: "Phone",
    display: (r) => r.phone,
    compare: (r) => r.normalized.phone ?? r.phone,
  },
  {
    key: "industry",
    label: "Industry",
    display: (r) => r.industry,
    compare: (r) => r.industry,
  },
  {
    key: "employees",
    label: "Employees",
    display: (r) => (r.employees === null ? null : r.employees.toLocaleString("en-US")),
    compare: (r) => r.employees,
  },
  {
    key: "revenue",
    label: "Revenue",
    display: (r) =>
      r.revenue === null
        ? null
        : r.revenue.toLocaleString("en-US", {
            style: "currency",
            currency: "USD",
            maximumFractionDigits: 0,
          }),
    compare: (r) => r.revenue,
  },
  {
    key: "source_system",
    label: "Source system",
    display: (r) => r.source_system,
    // Queue pairs are cross-source by construction; highlighting would always
    // read "differ" and dilute the signal, so this row stays neutral.
    compare: () => null,
  },
]

/** The server's exact mask token for PII fields under the analyst role. */
const MASK = "•••"

function isMasked(value: string | number | null): boolean {
  return value === MASK
}

type Agreement = "agree" | "differ" | "neutral"

function compareField(spec: FieldSpec, a: CompanyRecord, b: CompanyRecord): Agreement {
  const va = spec.compare(a)
  const vb = spec.compare(b)
  if (va === null || vb === null || isMasked(va) || isMasked(vb)) {
    return "neutral"
  }
  const same =
    typeof va === "string" && typeof vb === "string"
      ? va.trim().toLowerCase() === vb.trim().toLowerCase()
      : va === vb
  return same ? "agree" : "differ"
}

const ROW_TONES: Record<Agreement, string> = {
  agree: "bg-emerald-50",
  differ: "bg-amber-50",
  neutral: "",
}

const DOT_TONES: Record<Agreement, string> = {
  agree: "bg-emerald-500",
  differ: "bg-amber-500",
  neutral: "bg-border",
}

function FieldCell({ value }: { value: string | number | null }) {
  if (value === null || value === "") {
    return <span className="text-muted-foreground/60">—</span>
  }
  if (isMasked(value)) {
    return <span className="tracking-widest text-muted-foreground">{value}</span>
  }
  return <span className="break-words">{value}</span>
}

function RecordColumnHeader({
  record,
  slot,
  title,
}: {
  record: CompanyRecord
  slot: "record-a" | "record-b"
  title: string
}) {
  return (
    <div data-testid={slot} className="flex flex-col gap-1 px-3 py-2">
      <div className="flex items-center gap-2">
        <span className="text-xs font-semibold text-muted-foreground uppercase">
          {title}
        </span>
        <Badge variant="outline">{record.source_system}</Badge>
      </div>
      <span className="font-mono text-xs break-all text-muted-foreground">{record.record_id}</span>
    </div>
  )
}

function NotesBlock({ notes }: { notes: string | null }) {
  if (!notes) {
    return <div className="px-3 py-2" />
  }
  return (
    <blockquote className="mx-3 my-2 border-l-2 border-border pl-2.5 text-xs leading-relaxed text-muted-foreground italic">
      {notes}
    </blockquote>
  )
}

export function PairCard({ item }: { item: QueueItem }) {
  const { record_a: a, record_b: b } = item
  const hasNotes = Boolean(a.notes || b.notes)

  return (
    <Card data-testid="queue-card">
      <CardHeader className="border-b [.border-b]:pb-3">
        <CardTitle>
          Pair #{item.id}
          <span className="ml-2 text-sm font-normal text-muted-foreground">
            candidate duplicate — review the field evidence below
          </span>
        </CardTitle>
      </CardHeader>
      <CardContent className="overflow-x-auto px-0">
        {/* Wide comparison grid scrolls inside the card on narrow viewports
            instead of pushing Record B off-screen. */}
        <div className="grid min-w-[36rem] grid-cols-[8rem_1fr_1fr] text-sm sm:grid-cols-[9.5rem_1fr_1fr]">
          {/* Column headers */}
          <div className="border-b border-border" />
          <div className="border-b border-border">
            <RecordColumnHeader record={a} slot="record-a" title="Record A" />
          </div>
          <div className="border-b border-border">
            <RecordColumnHeader record={b} slot="record-b" title="Record B" />
          </div>

          {/* Field rows */}
          {FIELD_SPECS.map((spec) => {
            const agreement = compareField(spec, a, b)
            const tone = ROW_TONES[agreement]
            return (
              <div
                key={spec.key}
                data-testid={`field-row-${spec.key}`}
                className={`col-span-3 grid grid-cols-subgrid border-b border-border/60 last:border-b-0 ${tone}`}
              >
                <div className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium text-muted-foreground">
                  <span
                    aria-hidden
                    className={`size-1.5 shrink-0 rounded-full ${DOT_TONES[agreement]}`}
                  />
                  {spec.label}
                </div>
                <div className="px-3 py-1.5">
                  <FieldCell value={spec.display(a)} />
                </div>
                <div className="px-3 py-1.5">
                  <FieldCell value={spec.display(b)} />
                </div>
              </div>
            )
          })}

          {/* Attached notes, one quote block under each record column */}
          {hasNotes && (
            <>
              <div className="flex items-start px-3 py-2 text-xs font-medium text-muted-foreground">
                Notes
              </div>
              <NotesBlock notes={a.notes} />
              <NotesBlock notes={b.notes} />
            </>
          )}
        </div>
      </CardContent>
    </Card>
  )
}
