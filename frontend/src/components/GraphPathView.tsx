/**
 * Renders one knowledge-graph path as node/relationship pills.
 *
 * This is the visual form of the project's explainability claim: the user sees
 * the actual traversal (e.g. `fear -[ADDRESSES_EMOTION]- BG-2.47`) that caused
 * a verse to be retrieved, not a post-hoc rationalisation.
 */

import type { GraphPath } from '../types'

interface Props {
  path: GraphPath
}

export default function GraphPathView({ path }: Props) {
  const hasStructure = path.nodes.length > 0

  return (
    <div className="rounded-lg border border-white/10 bg-black/20 px-2.5 py-2">
      {hasStructure ? (
        <div className="flex flex-wrap items-center gap-1.5">
          {path.nodes.map((node, index) => (
            <span key={`${node}-${index}`} className="flex items-center gap-1.5">
              <span className="rounded bg-saffron-600/25 px-1.5 py-0.5 font-mono text-[11px] text-saffron-100">
                {node}
              </span>
              {index < path.relationships.length && (
                <span className="font-mono text-[10px] text-slate-500">
                  &minus;[{path.relationships[index]}]&rarr;
                </span>
              )}
            </span>
          ))}
          <span className="ml-auto text-[10px] text-slate-500">
            {path.hops} hop{path.hops === 1 ? '' : 's'}
          </span>
        </div>
      ) : (
        <p className="font-mono text-[11px] text-slate-400">{path.description}</p>
      )}

      {hasStructure && path.description && (
        <p className="mt-1.5 font-mono text-[10px] leading-relaxed text-slate-500">
          {path.description}
        </p>
      )}
    </div>
  )
}
