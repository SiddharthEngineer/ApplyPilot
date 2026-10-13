import { IMPORTANCE_LABELS, humanize } from '../labels';
import type { FitComponent, JobDetail } from '../types';
import FitBar from './FitBar';

const RATED = ['skills_match', 'experience_match', 'domain_interest'];

/** "4/5" for a Claude rating, ✓ / ✗ / ~ for a preference, "not set" when the criterion has no value. */
export function componentScore(c: FitComponent): string {
  if (c.score == null) return 'not set';
  if (RATED.includes(c.key)) return `${Math.round(c.score * 4 + 1)}/5`;
  if (c.key === 'red_flags') return c.score >= 1 ? 'none' : '⚠';
  return c.score >= 1 ? '✓' : c.score <= 0 ? '✗' : '~';
}

interface Props {
  job: JobDetail;
  busy: boolean; // a rescore is queued or running
  onRescore: () => void;
}

export default function FitBreakdown({ job, busy, onRescore }: Props) {
  const a = job.fit_assessment;
  const assessed = !!a && !a.prefiltered;
  const flags = a?.red_flags ?? [];
  return (
    <section className="card fit-card" aria-label="Fit">
      <div className="fit-head">
        <h2>Fit score <FitBar score={job.fit_score} /><span className="muted">/100</span></h2>
        <button type="button" onClick={onRescore} disabled={busy}
          title="Ask Claude to assess this job again with your current resume and preferences">
          Rescore
        </button>
      </div>
      {job.assessment_stale && (
        <p className="stale-note">Stale assessment: your resume or the inputs Claude judges have changed since this job
          was assessed. Rescore it, or re-assess every job from Preferences.</p>
      )}
      {job.flag_reasons.length > 0 && (
        <ul className="flag-list" aria-label="Flags">
          {job.flag_reasons.map((r) => <li key={r}>⚠ {r}</li>)}
        </ul>
      )}
      {assessed && a.summary && <p>{a.summary}</p>}
      {!assessed && !a?.prefiltered && job.score_reasoning && <p className="pre muted">{job.score_reasoning}</p>}
      {!a && <p className="muted">Not assessed by Claude yet; the score covers your preferences only.</p>}
      {job.fit_breakdown.length > 0 && (
        <ul className="breakdown">
          {job.fit_breakdown.map((c) => (
            <li key={c.key} className={c.dealbreaker_hit ? 'hit' : undefined}>
              <span className="bd-label">{c.label}</span>
              <span className="bd-score">{componentScore(c)}</span>
              <span className={`bd-importance imp-${c.importance}`}>{IMPORTANCE_LABELS[c.importance]}</span>
              <span className="bd-note muted">{c.note}</span>
            </li>
          ))}
        </ul>
      )}
      {flags.length > 0 && (
        <>
          <h3>Red flags</h3>
          <ul>{flags.map((f, i) => <li key={i}><strong>{humanize(f.code)}</strong>{f.note && `: ${f.note}`}</li>)}</ul>
        </>
      )}
    </section>
  );
}
