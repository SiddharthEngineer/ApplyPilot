import { useCallback, useEffect, useState, type ReactNode } from 'react';
import { Link, useParams } from 'react-router-dom';
import * as api from '../api';
import ActionBar from '../components/ActionBar';
import StatusBadge from '../components/StatusBadge';
import TaskProgress from '../components/TaskProgress';
import { day, humanize, roleLabel, STATUS_LABELS, WORK_MODE_LABELS } from '../labels';
import type { JobDetail, JobDetails, Task, UserStatus } from '../types';

function money(value: number, currency: string | null): string {
  try {
    return new Intl.NumberFormat('en-US', {
      style: 'currency', currency: currency || 'USD', maximumFractionDigits: value % 1 ? 2 : 0,
    }).format(value);
  } catch {
    return `${value.toLocaleString()} ${currency ?? ''}`.trim();
  }
}

export function compensation(d: JobDetails): string | null {
  const { salary_min: lo, salary_max: hi, salary_currency: cur, salary_period: period } = d;
  if (lo == null && hi == null) return null;
  const range = lo != null && hi != null && lo !== hi
    ? `${money(lo, cur)} – ${money(hi, cur)}`
    : lo != null && hi == null ? `from ${money(lo, cur)}`
      : lo == null && hi != null ? `up to ${money(hi, cur)}` : money(lo ?? hi!, cur);
  return period && period !== 'unknown' ? `${range} per ${period}` : range;
}

function years(d: JobDetails): string | null {
  const { years_experience_min: lo, years_experience_max: hi } = d;
  if (lo == null && hi == null) return null;
  if (lo != null && hi != null && lo !== hi) return `${lo}–${hi} years`;
  return lo != null ? `${lo}+ years` : `up to ${hi} years`;
}

const known = (v: string | null | undefined) => (v && v !== 'unknown' ? humanize(v) : null);

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="card">
      <h2>{title}</h2>
      {children}
    </section>
  );
}

function List({ items }: { items: string[] }) {
  return <ul>{items.map((item, i) => <li key={i}>{item}</li>)}</ul>;
}

/** Label/value rows, leaving out empty values. */
function Facts({ rows }: { rows: [string, ReactNode][] }) {
  const shown = rows.filter(([, v]) => v !== null && v !== undefined && v !== '');
  if (!shown.length) return null;
  return (
    <dl className="facts">
      {shown.map(([k, v]) => (
        <div key={k}><dt>{k}</dt><dd>{v}</dd></div>
      ))}
    </dl>
  );
}

function DetailSections({ d }: { d: JobDetails }) {
  const locations = d.locations.map((l) => [l.city, l.state, l.country].filter(Boolean).join(', ')).filter(Boolean);
  const pay = compensation(d);
  const exp = years(d);
  const edu = [known(d.education_level), d.education_fields.join(', ')].filter(Boolean).join(' · ');
  const sections: [string, boolean, ReactNode][] = [
    ['Summary', !!(d.summary || d.company_blurb), <>
      {d.summary && <p>{d.summary}</p>}
      {d.company_blurb && <p className="muted">{d.company_blurb}</p>}
    </>],
    ['Responsibilities', d.responsibilities.length > 0, <List items={d.responsibilities} />],
    ['Required qualifications', d.required_qualifications.length > 0, <List items={d.required_qualifications} />],
    ['Preferred qualifications', d.preferred_qualifications.length > 0, <List items={d.preferred_qualifications} />],
    ['Skills', d.skills_required.length + d.skills_preferred.length > 0, <div className="chips">
      {d.skills_required.map((s) => <span key={`r-${s}`} className="chip">{s}</span>)}
      {d.skills_preferred.map((s) => <span key={`p-${s}`} className="chip chip-preferred" title="Preferred">{s}</span>)}
    </div>],
    ['Compensation', !!pay, <p>{pay}</p>],
    ['Location and work mode', !!(locations.length || d.remote_region || known(d.work_mode)), <Facts rows={[
      ['Work mode', WORK_MODE_LABELS[d.work_mode] && d.work_mode !== 'unknown' ? WORK_MODE_LABELS[d.work_mode] : null],
      ['Locations', locations.join(' · ') || null],
      ['Remote region', d.remote_region],
      ['Travel', d.travel],
    ]} />],
    ['Seniority and type', !!(known(d.seniority) || known(d.employment_type) || d.start_date), <Facts rows={[
      ['Seniority', known(d.seniority)],
      ['Employment', known(d.employment_type)],
      ['Start date', d.start_date],
    ]} />],
    ['Education and experience', !!(edu || exp), <Facts rows={[['Education', edu || null], ['Experience', exp]]} />],
    ['Sponsorship and clearance', !!(known(d.visa_sponsorship) || known(d.security_clearance)), <Facts rows={[
      ['Visa sponsorship', known(d.visa_sponsorship)],
      ['Security clearance', known(d.security_clearance)],
    ]} />],
    ['Benefits', d.benefits.length > 0, <List items={d.benefits} />],
    ['Team', !!d.team, <p>{d.team}</p>],
  ];
  return <>{sections.filter(([, show]) => show).map(([title, , body]) => <Section key={title} title={title}>{body}</Section>)}</>;
}

function LinkRow({ job }: { job: JobDetail }) {
  const links: [string, string | null][] = [
    ['Posting', job.links.posting],
    ['Apply', job.links.apply],
    ['Drive folder', job.links.drive_folder],
    ['Resume', job.links.resume],
    ['Cover letter', job.links.cover_letter],
  ];
  return (
    <nav className="link-row" aria-label="Links">
      {links.filter(([, href]) => href).map(([label, href]) => (
        <a key={label} href={href!} target="_blank" rel="noreferrer noopener">{label} ↗</a>
      ))}
    </nav>
  );
}

export default function JobPage() {
  const { key = '' } = useParams();
  const [job, setJob] = useState<JobDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [task, setTask] = useState<Task | null>(null); // the latest generation task for this job
  const [notes, setNotes] = useState('');

  const load = useCallback(async (signal?: AbortSignal) => {
    const [detail, tasks] = await Promise.all([api.getJob(key, signal), api.listJobTasks(key, signal)]);
    setJob(detail);
    setNotes(detail.notes ?? '');
    setTask(tasks[0] ?? null);
  }, [key]);

  useEffect(() => {
    const ctrl = new AbortController();
    setJob(null);
    setError(null);
    load(ctrl.signal).catch((err) => {
      if (!ctrl.signal.aborted) setError(err.message || String(err));
    });
    return () => ctrl.abort();
  }, [load]);

  async function act(fn: () => Promise<JobDetail | void>) {
    setBusy(true);
    setActionError(null);
    try {
      const updated = await fn();
      if (updated) setJob(updated);
    } catch (err) {
      setActionError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  const onGenerate = (what: { resume: boolean; cover: boolean }) =>
    act(async () => {
      const t = await api.generate(key, what);
      setTask(t);
      return api.getJob(key); // the job is now In progress
    });
  const onStatus = (status: UserStatus | null, submittedAt?: string) => act(() => api.setStatus(key, status, submittedAt));
  const onTaskFinish = useCallback((t: Task) => {
    setTask(t);
    api.getJob(key).then((d) => setJob(d)).catch(() => {}); // new Drive links
  }, [key]);

  if (error) {
    return <p className="notice error" role="alert">Couldn't load this job: {error}. <Link to="/">Back to jobs</Link></p>;
  }
  if (!job) return <p className="notice">Loading job…</p>;

  const generating = task?.state === 'queued' || task?.state === 'running';
  const d = job.details;

  return (
    <article className="job-page">
      <Link to="/" className="back">← Jobs</Link>
      <header className="job-header">
        <div className="job-title">
          <h1>{job.title || '(untitled)'}</h1>
          <p className="muted">
            {[job.company, job.location, roleLabel(job.role_category)].filter(Boolean).join(' · ')}
            {job.deadline && <> · due {day(job.deadline)}</>}
            {job.discovered_at && <> · found {day(job.discovered_at)}</>}
          </p>
        </div>
        <div className="job-meta">
          <StatusBadge status={job.status} color={job.status_color} />
          {job.days_since_submitted != null && (
            <span className="muted">{job.days_since_submitted} days since submitted</span>
          )}
        </div>
      </header>

      {job.fit_score != null && (
        <details className="fit">
          <summary>Fit score <strong>{job.fit_score}</strong>/10</summary>
          <p className="pre">{job.score_reasoning || 'No reasoning recorded.'}</p>
        </details>
      )}
      <LinkRow job={job} />

      <ActionBar job={job} generating={generating} busy={busy} onGenerate={onGenerate} onStatus={onStatus} />
      {task && <TaskProgress task={task} onFinish={onTaskFinish} />}
      {actionError && <p className="error" role="alert">{actionError}</p>}

      {job.submitted_date && (
        <label className="inline-field">
          Submitted on{' '}
          <input
            type="date"
            aria-label="Submitted date"
            value={day(job.submitted_date)}
            disabled={busy}
            onChange={(e) => {
              const value = e.target.value;
              if (value) act(() => api.patchJob(key, { submitted_at: value }));
            }}
          />
        </label>
      )}

      <label className="notes">
        <span>Notes</span>
        <textarea
          rows={3}
          value={notes}
          placeholder="Recruiter name, referral, interview dates…"
          onChange={(e) => setNotes(e.target.value)}
          onBlur={() => {
            if (notes !== (job.notes ?? '')) act(() => api.patchJob(key, { notes: notes || null }));
          }}
        />
      </label>

      <div className="sections">
        {d ? (
          <DetailSections d={d} />
        ) : (
          <Section title="Description">
            <p className="muted">Not extracted yet. Run <code>applypilot run extract</code> to fill these sections.</p>
            <p className="pre">{job.description_text || 'No description was scraped for this job.'}</p>
          </Section>
        )}
        {d && job.description_text && (
          <details className="card">
            <summary><h2>Full posting</h2></summary>
            <p className="pre">{job.description_text}</p>
          </details>
        )}
      </div>

      <Section title="Status history">
        {job.events.length ? (
          <ol className="history">
            {job.events.map((e, i) => (
              <li key={i}>
                <time>{e.at.slice(0, 16).replace('T', ' ')}</time>{' '}
                {STATUS_LABELS[e.from_status as keyof typeof STATUS_LABELS] ?? e.from_status ?? '—'} →{' '}
                <strong>{STATUS_LABELS[e.to_status as keyof typeof STATUS_LABELS] ?? e.to_status ?? 'Reset'}</strong>
                {e.source && <span className="muted"> ({e.source})</span>}
              </li>
            ))}
          </ol>
        ) : (
          <p className="muted">No changes yet.</p>
        )}
      </Section>
    </article>
  );
}
