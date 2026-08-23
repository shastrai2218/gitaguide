/**
 * Emotion intensity as a bar, with the safety threshold marked.
 *
 * The threshold marker matters: it shows the reader *why* a turn was (or was
 * not) short-circuited by the gate's model layer.
 */

interface Props {
  value: number
  threshold?: number
  label?: string
}

function toneFor(value: number): string {
  if (value >= 0.85) return 'bg-red-500'
  if (value >= 0.6) return 'bg-orange-400'
  if (value >= 0.35) return 'bg-amber-300'
  return 'bg-emerald-400'
}

export default function IntensityBar({ value, threshold = 0.85, label = 'Intensity' }: Props) {
  const percent = Math.round(Math.min(1, Math.max(0, value)) * 100)

  return (
    <div>
      <div className="mb-1 flex items-baseline justify-between">
        <span className="label">{label}</span>
        <span className="font-mono text-xs text-slate-300">{value.toFixed(2)}</span>
      </div>

      <div
        className="relative h-2.5 w-full overflow-hidden rounded-full bg-white/10"
        role="meter"
        aria-valuenow={percent}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-label={`${label} ${percent} percent`}
      >
        <div
          className={`h-full rounded-full transition-all duration-500 ${toneFor(value)}`}
          style={{ width: `${percent}%` }}
        />
        <div
          className="absolute top-0 h-full w-0.5 bg-white/70"
          style={{ left: `${threshold * 100}%` }}
          title={`Safety gate threshold (${threshold.toFixed(2)})`}
        />
      </div>

      <p className="mt-1 text-[11px] text-slate-500">
        White line = gate threshold ({threshold.toFixed(2)}). Above it, extreme grief or fear
        alone short-circuits the pipeline.
      </p>
    </div>
  )
}
