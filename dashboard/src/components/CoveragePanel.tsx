import type { Coverage } from '../types';

interface Props {
  coverage: Coverage;
  unmet: string[]; // qualifications nothing in the library or base resume backs
}

const pct = (v: number | null) => (v == null ? 'n/a' : `${Math.round(v)}%`);

/** How the saved draft covers the posting's qualifications. Report-only: unmet ones are never written in. */
export default function CoveragePanel({ coverage, unmet }: Props) {
  return (
    <section className="card coverage" aria-label="Resume targeting">
      <h3>Resume targeting</h3>
      <p>
        <strong>{pct(coverage.pct_required)}</strong> of the required qualifications are on your resume
        {coverage.pct_preferred != null && <>; {pct(coverage.pct_preferred)} of the preferred</>}.
      </p>
      {coverage.covered.length > 0 && (
        <div className="chips" aria-label="Covered">
          {coverage.covered.map((t) => <span key={t} className="chip">{t}</span>)}
        </div>
      )}
      {unmet.length > 0 && (
        <>
          <h4>Not on your resume</h4>
          <p className="muted small">Nothing in your content library backs these, so they stay off. Add them to the
            library if you have the experience.</p>
          <ul aria-label="Not on your resume">{unmet.map((t) => <li key={t}>{t}</li>)}</ul>
        </>
      )}
    </section>
  );
}
