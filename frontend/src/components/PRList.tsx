import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { listDeployments } from '../api'
import type { DeploymentStatus, ShadowDeployment } from '../types'

const STATUS_STYLES: Record<DeploymentStatus, string> = {
  active: 'bg-emerald-600/20 text-emerald-400 border border-emerald-600/40',
  closed: 'bg-gray-700/40 text-gray-400 border border-gray-600/40',
  error: 'bg-red-600/20 text-red-400 border border-red-600/40',
}

export default function PRList() {
  const [deployments, setDeployments] = useState<ShadowDeployment[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const navigate = useNavigate()

  useEffect(() => {
    let cancelled = false

    async function load() {
      try {
        const data = await listDeployments()
        if (!cancelled) setDeployments(data)
      } catch (err) {
        if (!cancelled) setError(String(err))
      } finally {
        if (!cancelled) setLoading(false)
      }
    }

    load()
    return () => { cancelled = true }
  }, [])

  if (loading) {
    return (
      <div className="flex items-center justify-center py-24 text-gray-500">
        <svg className="h-5 w-5 animate-spin mr-2" fill="none" viewBox="0 0 24 24">
          <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
          <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8H4z" />
        </svg>
        Loading deployments…
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

  if (deployments.length === 0) {
    return (
      <div className="py-24 text-center text-gray-500">
        <p className="text-lg font-medium">No shadow deployments yet.</p>
        <p className="mt-1 text-sm">Open a pull request on your monitored repo to get started.</p>
      </div>
    )
  }

  return (
    <div>
      <h1 className="mb-6 text-xl font-semibold text-white">Active Shadow Deployments</h1>
      <div className="overflow-hidden rounded-xl border border-gray-800">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-gray-800 bg-gray-900 text-left text-xs font-medium uppercase tracking-wider text-gray-500">
              <th className="px-6 py-3">PR</th>
              <th className="px-6 py-3">Repo / Branch</th>
              <th className="px-6 py-3">Shadow URL</th>
              <th className="px-6 py-3">Status</th>
              <th className="px-6 py-3">Created</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-800/60">
            {deployments.map((dep) => (
              <tr
                key={dep.id}
                onClick={() => navigate(`/deployment/${dep.id}`)}
                className="cursor-pointer bg-gray-900/30 transition-colors hover:bg-gray-800/60"
              >
                <td className="px-6 py-4">
                  <div className="font-medium text-white">#{dep.pr_number}</div>
                  <div className="mt-0.5 max-w-xs truncate text-gray-400">{dep.pr_title}</div>
                </td>
                <td className="px-6 py-4 font-mono text-gray-300">
                  <div>{dep.repo}</div>
                  <div className="text-gray-500">{dep.branch}</div>
                </td>
                <td className="px-6 py-4">
                  <a
                    href={dep.shadow_url}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="font-mono text-indigo-400 hover:underline"
                    onClick={(e) => e.stopPropagation()}
                  >
                    {dep.shadow_url}
                  </a>
                </td>
                <td className="px-6 py-4">
                  <span className={`inline-flex rounded-full px-2.5 py-0.5 text-xs font-medium ${STATUS_STYLES[dep.status]}`}>
                    {dep.status}
                  </span>
                </td>
                <td className="px-6 py-4 text-gray-400">
                  {new Date(dep.created_at).toLocaleDateString()}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}
