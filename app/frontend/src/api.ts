// Typed client for the review-queue API. Same-origin base path `/api`
// (proxied to the FastAPI server by Vite in development).

export type Role = "steward" | "analyst"

export type Decision = "approve" | "reject" | "skip"

export type QueueStatus = "pending" | "approved" | "rejected" | "skipped"

export interface NormalizedFields {
  name: string | null
  domain: string | null
  phone: string | null
  address: string | null
}

export interface CompanyRecord {
  record_id: string
  source_system: string
  company_name: string | null
  domain: string | null
  address: string | null
  phone: string | null
  industry: string | null
  employees: number | null
  revenue: number | null
  notes: string | null
  normalized: NormalizedFields
  pii_flags: string[]
}

export interface AgentDecision {
  match: boolean
  confidence: number
  evidence: string[]
  reason: string
  decided_by: string
}

export interface QueueItem {
  id: number
  record_a: CompanyRecord
  record_b: CompanyRecord
  agent: AgentDecision
  status: QueueStatus
  created_at: string
  reviewed_at: string | null
}

export interface PipelineMetrics {
  precision: number | null
  recall: number | null
  false_positives: number | null
  agent_band_size: number | null
}

export interface Stats {
  pending: number
  approved: number
  rejected: number
  skipped: number
  decisions_pending_ingestion: number
  metrics: PipelineMetrics | null
}

export class ApiError extends Error {
  readonly status: number

  constructor(status: number, message: string) {
    super(message)
    this.name = "ApiError"
    this.status = status
  }
}

async function parseErrorDetail(res: Response): Promise<string> {
  try {
    const body: unknown = await res.json()
    if (
      typeof body === "object" &&
      body !== null &&
      "detail" in body &&
      typeof (body as { detail: unknown }).detail === "string"
    ) {
      return (body as { detail: string }).detail
    }
  } catch {
    // Non-JSON error body; fall through to the status line.
  }
  return `Request failed with status ${res.status}`
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, init)
  if (!res.ok) {
    throw new ApiError(res.status, await parseErrorDetail(res))
  }
  return (await res.json()) as T
}

export function fetchStats(role: Role): Promise<Stats> {
  return request<Stats>(`/api/stats?role=${role}`)
}

/** Returns the next pending pair, or null when the queue is drained (404). */
export async function fetchNextPair(role: Role): Promise<QueueItem | null> {
  try {
    return await request<QueueItem>(`/api/queue/next?role=${role}`)
  } catch (err) {
    if (err instanceof ApiError && err.status === 404) {
      return null
    }
    throw err
  }
}

export function postDecision(id: number, decision: Decision): Promise<QueueItem> {
  return request<QueueItem>(`/api/queue/${id}/decision`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ decision }),
  })
}
