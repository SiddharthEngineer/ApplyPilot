/** A 0-100 fit score as a number with a small colour bar (green 70+, gold 50+, red below). */
export function fitLevel(score: number): 'high' | 'mid' | 'low' {
  return score >= 70 ? 'high' : score >= 50 ? 'mid' : 'low';
}

export default function FitBar({ score, stale = false }: { score: number | null; stale?: boolean }) {
  if (score == null) return <>—</>;
  return (
    <span className="fit-score" title={stale ? 'Assessed before your last preference change' : undefined}>
      <span className="fit-bar" aria-hidden="true">
        <span className={`fit-bar-fill fit-${fitLevel(score)}`} style={{ width: `${Math.max(0, Math.min(100, score))}%` }} />
      </span>
      {score}
      {stale && <span className="stale-dot" aria-label="stale assessment">*</span>}
    </span>
  );
}
