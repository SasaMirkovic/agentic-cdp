// TanStack Query hooks for the review-queue screen.

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { toast } from "sonner"
import {
  ApiError,
  fetchNextPair,
  fetchStats,
  postDecision,
  type Decision,
  type QueueItem,
  type Role,
  type Stats,
} from "@/api"

export const statsKey = (role: Role) => ["stats", role] as const
export const nextPairKey = (role: Role) => ["queue-next", role] as const

export function useStats(role: Role) {
  return useQuery<Stats>({
    queryKey: statsKey(role),
    queryFn: () => fetchStats(role),
  })
}

export function useNextPair(role: Role) {
  return useQuery<QueueItem | null>({
    queryKey: nextPairKey(role),
    queryFn: () => fetchNextPair(role),
  })
}

/**
 * Records a decision for a queue item, then advances the screen by
 * invalidating the stats and next-pair queries. On failure the caches are
 * left untouched (no advance) and the error surfaces as a toast.
 */
export function useDecisionMutation(role: Role) {
  const queryClient = useQueryClient()

  const advance = () =>
    Promise.all([
      queryClient.invalidateQueries({ queryKey: ["stats"] }),
      queryClient.invalidateQueries({ queryKey: nextPairKey(role) }),
    ])

  return useMutation({
    // Fail immediately when offline instead of silently replaying on reconnect.
    networkMode: "always",
    mutationFn: ({ id, decision }: { id: number; decision: Decision }) =>
      postDecision(id, decision),
    onSuccess: async () => {
      await advance()
    },
    onError: async (error: Error) => {
      if (error instanceof ApiError && error.status === 409) {
        // Already decided (e.g. a duplicate submit): the decision stands; move on.
        toast.info("Already decided — advancing")
        await advance()
        return
      }
      toast.error("Decision was not saved", {
        description: error.message,
      })
    },
  })
}
