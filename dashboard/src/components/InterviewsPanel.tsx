import { useState, type FormEvent } from 'react';
import * as api from '../api';
import { localDateTime, OUTCOME_LABELS } from '../labels';
import type { Interview, InterviewFields, JobDetail, Outcome } from '../types';
import BlurField from './BlurField';

const OUTCOMES = Object.keys(OUTCOME_LABELS) as Outcome[];

/** A `datetime-local` value (the browser's time zone) as an ISO UTC string. */
export function localInputToIso(value: string): string | null {
  const d = new Date(value);
  return value && !Number.isNaN(d.getTime()) ? d.toISOString() : null;
}

/** An ISO timestamp as a `datetime-local` value in the browser's time zone. */
export function isoToLocalInput(value: string | null): string {
  if (!value) return '';
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return '';
  const pad = (n: number) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

function isPast(iv: Interview, now: number): boolean {
  return !!iv.scheduled_at && Date.parse(iv.scheduled_at) < now;
}

function AddInterviewForm({ onAdd, onCancel }: { onAdd: (fields: InterviewFields) => Promise<void>; onCancel: () => void }) {
  const [when, setWhen] = useState('');
  const [stage, setStage] = useState('');
  const [format, setFormat] = useState('');
  const [interviewers, setInterviewers] = useState('');
  const [busy, setBusy] = useState(false);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    const fields: InterviewFields = { scheduled_at: localInputToIso(when) };
    if (stage.trim()) fields.stage = stage.trim();
    if (format.trim()) fields.format = format.trim();
    if (interviewers.trim()) fields.interviewers = interviewers.trim();
    try {
      await onAdd(fields);
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="interview-form" aria-label="Add interview" onSubmit={submit}>
      <div className="form-grid">
        <label className="field"><span>Date and time</span>
          <input type="datetime-local" value={when} onChange={(e) => setWhen(e.target.value)} /></label>
        <label className="field"><span>Stage</span>
          <input placeholder="Phone screen, onsite…" value={stage} onChange={(e) => setStage(e.target.value)} /></label>
        <label className="field"><span>Format</span>
          <input placeholder="Video, phone, in person" value={format} onChange={(e) => setFormat(e.target.value)} /></label>
        <label className="field"><span>Interviewers</span>
          <input value={interviewers} onChange={(e) => setInterviewers(e.target.value)} /></label>
      </div>
      <div className="dialog-actions">
        <button type="submit" className="primary" disabled={busy}>Save interview</button>
        <button type="button" disabled={busy} onClick={onCancel}>Cancel</button>
      </div>
    </form>
  );
}

function InterviewItem({ iv, onSave, onDelete }: {
  iv: Interview;
  onSave: (fields: InterviewFields) => void;
  onDelete: () => void;
}) {
  const heading = [iv.stage || 'Interview', iv.format].filter(Boolean).join(' · ');
  return (
    <li className="interview" aria-label={heading}>
      <div className="contact-head">
        <strong>{heading}</strong>
        <time className="muted" dateTime={iv.scheduled_at ?? undefined}>{localDateTime(iv.scheduled_at)}</time>
        {iv.interviewers && <span className="muted">with {iv.interviewers}</span>}
        <span className="contact-actions">
          <select aria-label="Outcome" value={iv.outcome ?? 'pending'}
            onChange={(e) => onSave({ outcome: e.target.value as Outcome })}>
            {OUTCOMES.map((o) => <option key={o} value={o}>{OUTCOME_LABELS[o]}</option>)}
          </select>
          <button type="button" className="chip-remove" aria-label={`Delete ${heading}`} onClick={onDelete}>×</button>
        </span>
      </div>
      <div className="form-grid">
        <label className="field"><span>Date and time</span>
          <input type="datetime-local" defaultValue={isoToLocalInput(iv.scheduled_at)}
            onBlur={(e) => {
              const next = localInputToIso(e.target.value);
              if (next !== iv.scheduled_at && isoToLocalInput(next) !== isoToLocalInput(iv.scheduled_at)) {
                onSave({ scheduled_at: next });
              }
            }} /></label>
        <BlurField label="Stage" value={iv.stage} onSave={(v) => onSave({ stage: v })} />
        <BlurField label="Format" value={iv.format} onSave={(v) => onSave({ format: v })} />
        <BlurField label="Interviewers" value={iv.interviewers} onSave={(v) => onSave({ interviewers: v })} />
      </div>
      <BlurField label="Prep notes" multiline value={iv.prep_notes} onSave={(v) => onSave({ prep_notes: v })} />
      <BlurField label="Debrief notes" multiline value={iv.debrief_notes} onSave={(v) => onSave({ debrief_notes: v })} />
    </li>
  );
}

/** The job's interviews by date; past ones collapse. Text saves on blur. */
export default function InterviewsPanel({ job, onChange }: { job: JobDetail; onChange: (job: JobDetail) => void }) {
  const [adding, setAdding] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const now = Date.now();
  const past = job.interviews.filter((iv) => isPast(iv, now));
  const upcoming = job.interviews.filter((iv) => !isPast(iv, now));

  async function run(fn: () => Promise<unknown>) {
    setError(null);
    try {
      await fn();
      onChange(await api.getJob(job.key));
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }

  const item = (iv: Interview) => (
    <InterviewItem key={iv.id} iv={iv}
      onSave={(fields) => run(() => api.updateInterview(iv.id, fields))}
      onDelete={() => {
        if (window.confirm('Delete this interview and its notes?')) run(() => api.deleteInterview(iv.id));
      }} />
  );

  return (
    <section className="card" aria-labelledby="interviews-title">
      <div className="card-head">
        <h2 id="interviews-title">Interviews</h2>
        {!adding && <button type="button" onClick={() => setAdding(true)}>Add interview</button>}
      </div>
      {adding && (
        <AddInterviewForm onCancel={() => setAdding(false)}
          onAdd={(fields) => run(async () => {
            await api.createInterview(job.key, fields);
            setAdding(false);
          })} />
      )}
      {upcoming.length > 0 && <ul className="interview-list">{upcoming.map(item)}</ul>}
      {past.length > 0 && (
        <details className="past-interviews">
          <summary>Past interviews ({past.length})</summary>
          <ul className="interview-list">{past.map(item)}</ul>
        </details>
      )}
      {!job.interviews.length && !adding && <p className="muted">No interviews yet.</p>}
      {error && <p className="error" role="alert">{error}</p>}
    </section>
  );
}
