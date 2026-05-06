import ReactDiffViewer, { DiffMethod } from 'react-diff-viewer-continued'
import type { ResponsePair } from '../types'

interface ResponseDiffProps {
  pair: ResponsePair
}

function formatBody(body: string): string {
  // Attempt to pretty-print JSON bodies for readability.
  try {
    return JSON.stringify(JSON.parse(body), null, 2)
  } catch {
    return body
  }
}

export default function ResponseDiff({ pair }: ResponseDiffProps) {
  const prodFormatted = formatBody(pair.prod_body)
  const shadowFormatted = formatBody(pair.shadow_body)

  const statusMatch = pair.prod_status === pair.shadow_status

  return (
    <div className="rounded-lg border border-gray-800 overflow-hidden">
      {/* Path + method bar */}
      <div className="flex items-center gap-3 bg-gray-900 px-4 py-2 border-b border-gray-800">
        <span className="rounded bg-gray-700 px-2 py-0.5 text-xs font-mono font-semibold text-gray-300">
          {pair.request_method}
        </span>
        <span className="font-mono text-sm text-gray-300">{pair.request_path}</span>
        <span className="ml-auto text-xs text-gray-500">
          {new Date(pair.captured_at).toLocaleString()}
        </span>
      </div>

      {/* Status code row */}
      <div className="flex gap-6 bg-gray-900/50 px-4 py-2 border-b border-gray-800 text-sm">
        <span>
          <span className="text-gray-500">Prod status: </span>
          <span className={pair.prod_status >= 400 ? 'text-red-400' : 'text-emerald-400'}>
            {pair.prod_status}
          </span>
        </span>
        <span>
          <span className="text-gray-500">Shadow status: </span>
          <span className={pair.shadow_status >= 400 ? 'text-red-400 font-semibold' : 'text-emerald-400'}>
            {pair.shadow_status}
          </span>
        </span>
        {!statusMatch && (
          <span className="ml-auto rounded bg-amber-900/40 px-2 py-0.5 text-xs text-amber-400 border border-amber-700/40">
            Status mismatch
          </span>
        )}
      </div>

      {/* Diff viewer */}
      <div className="text-sm [&_.rdw-editor-main]:bg-transparent">
        <ReactDiffViewer
          oldValue={prodFormatted}
          newValue={shadowFormatted}
          splitView={true}
          leftTitle="Production"
          rightTitle="Shadow"
          compareMethod={DiffMethod.WORDS}
          useDarkTheme={true}
          styles={{
            variables: {
              dark: {
                diffViewerBackground: '#0f172a',
                diffViewerTitleBackground: '#1e293b',
                diffViewerTitleColor: '#94a3b8',
                addedBackground: '#14532d33',
                addedColor: '#86efac',
                removedBackground: '#7f1d1d33',
                removedColor: '#fca5a5',
                wordAddedBackground: '#16a34a55',
                wordRemovedBackground: '#dc262655',
                codeFoldBackground: '#1e293b',
                codeFoldGutterBackground: '#1e293b',
                codeFoldContentColor: '#475569',
                gutterBackground: '#1e293b',
                gutterBackgroundDark: '#1e293b',
                gutterColor: '#475569',
              },
            },
          }}
        />
      </div>
    </div>
  )
}
