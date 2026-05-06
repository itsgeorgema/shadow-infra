import type { VerdictType } from '../types'

interface VerdictBadgeProps {
  verdict: VerdictType
  size?: 'sm' | 'md'
}

const STYLES: Record<VerdictType, string> = {
  Safe: 'bg-emerald-600/20 text-emerald-400 border border-emerald-600/40',
  Warning: 'bg-amber-600/20 text-amber-400 border border-amber-600/40',
  Critical: 'bg-red-600/20 text-red-400 border border-red-600/40',
}

const DOTS: Record<VerdictType, string> = {
  Safe: 'bg-emerald-400',
  Warning: 'bg-amber-400',
  Critical: 'bg-red-400',
}

export default function VerdictBadge({ verdict, size = 'md' }: VerdictBadgeProps) {
  const padding = size === 'sm' ? 'px-2 py-0.5 text-xs' : 'px-3 py-1 text-sm'

  return (
    <span className={`inline-flex items-center gap-1.5 rounded-full font-medium ${padding} ${STYLES[verdict]}`}>
      <span className={`h-1.5 w-1.5 rounded-full ${DOTS[verdict]}`} />
      {verdict}
    </span>
  )
}
