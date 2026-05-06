import { useEffect, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { getPairsForDeployment, getVerdictForPair, listDeployments } from '../api'
import type { ResponsePair, ShadowDeployment, Verdict } from '../types'
import ResponseDiff from './ResponseDiff'
import VerdictBadge from './VerdictBadge'

interface PairWithVerdict {
  pair: ResponsePair
  verdict: Verdict | null
}

export default function DriftReport() {
  const { id } = useParams<{ id: string }>()
  const navigate = useNavigate()

  const [deployment, setDeployment] = useState<ShadowDeployment | null>(null)
  const [items, setItems] = useState<PairWithVerdict[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!id) return
    let cancelled = false

    async function load() {
      try {
        // Load deployment metadata and response pairs in parallel.
        const [allDeployments, pairs] = await Promise.all([
          listDeployments(),
          getPairsForDeployment(id!),
        ])

        if (cancelled) return

        const dep = allDeployments.find((d) => d.id === id) ?? null
        setDeployment(dep)

        // Fetch verdicts for each pair.
        const verdicts = await Promise.all(
          pairs.map((p) => getVerdictForPair(p.id).catch(() => null)),
        )

        if (!cancelled) {
          setItems(pairs.map((pair, i) => ({ pair, verdict: verdicts[i] })))
        }
      } catch (err) {
        if (!cancelled) setError(String(err))
      } finally {
        if (!cancelled) setLoading(false)
      }
    }

    load()
    return () => { cancelled = true }
  }, [id])

  // Summary counts
  const counts = items.reduce(
    (acc, { verdict }) => {
      if (verdict) acc[verdict.verdict] = (acc[verdict.verdict] ?? 0) + 1
      return acc
    },
    {} as Record<string, number>,
  )

  if (loading) {
    return (
      <div className="flex items-center justify-center py-24 text-gray-500">
        <svg className="h-5 w-5 animate-spin mr-2" fill="none" viewBox="0 0 24 24">
          <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
          <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8H4z" />
        </svg>
        Loading drift report…
      </div>
    )
  }

  if (error) {
    return (
      <div className="rounded-lg border border-red-800 bg-red-900/20 px-6 py-4 text-red-400">
        <strong>Error:</strong> {error}
      </div>
    )
  }

  return (
    <div>
      {/* Back button */}
      <button
        onClick={() => navigate('/')}
        className="mb-6 flex items-center gap-1.5 text-sm text-gray-400 hover:text-white transition-colors"
      >
        <svg className="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 19l-7-7 7-7" />
        </svg>
        All deployments
      </button>

      {/* Deployment header */}
      {deployment && (
        <div className="mb-8 rounded-xl border border-gray-800 bg-gray-900/50 px-6 py-5">
          <div className="flex items-start justify-between gap-4">
            <div>
              <h1 className="text-xl font-semibold text-white">
                PR #{deployment.pr_number}: {deployment.pr_title}
              </h1>
              <p className="mt-1 font-mono text-sm text-gray-400">
                {deployment.repo} @ {deployment.branch}
              </p>
              <p className="mt-1 text-sm text-gray-500">
                Shadow URL:{' '}
                <a
                  href={deployment.shadow_url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="text-indigo-400 hover:underline"
                >
                  {deployment.shadow_url}
                </a>
              </p>
            </div>
            <div className="flex flex-col items-end gap-2 shrink-0">
              {/* Verdict summary pills */}
              {(['Critical', 'Warning', 'Safe'] as const).map((v) =>
                counts[v] ? (
                  <div key={v} className="flex items-center gap-2">
                    <VerdictBadge verdict={v} size="sm" />
                    <span className="text-sm text-gray-400">{counts[v]}</span>
                  </div>
                ) : null,
              )}
            </div>
          </div>
        </div>
      )}

      {/* Response pairs */}
      {items.length === 0 ? (
        <div className="py-16 text-center text-gray-500">
          <p className="text-lg">No response pairs captured yet.</p>
          <p className="mt-1 text-sm">
            Traffic will appear here once the shadow starts receiving mirrored requests.
          </p>
        </div>
      ) : (
        <div className="space-y-6">
          {items.map(({ pair, verdict }) => (
            <div key={pair.id} className="space-y-3">
              {/* Verdict row */}
              <div className="flex items-start gap-4">
                {verdict ? (
                  <>
                    <VerdictBadge verdict={verdict.verdict} />
                    <div className="text-sm text-gray-400">
                      <p>{verdict.reasoning}</p>
                      {verdict.diff_summary && (
                        <pre className="mt-1 whitespace-pre-wrap font-mono text-xs text-gray-500">
                          {verdict.diff_summary}
                        </pre>
                      )}
                    </div>
                  </>
                ) : (
                  <span className="rounded-full border border-gray-700 px-3 py-1 text-xs text-gray-500">
                    Pending analysis
                  </span>
                )}
              </div>

              {/* Side-by-side diff */}
              <ResponseDiff pair={pair} />
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
