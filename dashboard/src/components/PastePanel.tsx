import { useState, type FormEvent } from 'react';
import { putDescription } from '../api';
import type { JobDetail, Task } from '../types';

export const MIN_DESCRIPTION = 200;

/** Shown when the posting couldn't be read: say why, link to it, and take a pasted description instead. */
export default function PastePanel({ job, onSaved }: { job: JobDetail; onSaved: (task: Task) => void }) {
  const [title, setTitle] = useState(job.title ?? '');
  const [company, setCompany] = useState(job.company && job.company !== 'Unknown company' ? job.company : '');
  const [location, setLocation] = useState(job.location ?? '');
  const [description, setDescription] = useState(job.description_text ?? '');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const isWeb = /^https?:\/\//.test(job.url);
  const reason = job.intake_status === 'error'
    ? `Something went wrong: ${job.intake_error ?? 'unknown error'}`
    : job.intake_error ?? 'The page had no readable posting.';

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (description.trim().length < MIN_DESCRIPTION) {
      setError(`Paste the whole description (at least ${MIN_DESCRIPTION} characters).`);
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const res = await putDescription(job.key, {
        title: title.trim() || undefined, company: company.trim() || undefined,
        location: location.trim() || undefined, description: description.trim(),
      });
      onSaved(res.task);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="card paste-panel" aria-labelledby="paste-title" onSubmit={submit}>
      <h2 id="paste-title">We couldn't read this posting</h2>
      <p>{reason}</p>
      <p>
        {isWeb && <><a href={job.url} target="_blank" rel="noreferrer noopener">Open posting ↗</a>, copy the description, and </>}
        {isWeb ? 'paste' : 'Paste'} it here. Strelitzia will extract and score it.
      </p>
      <div className="form-grid">
        <label className="field"><span>Title</span>
          <input value={title} onChange={(e) => setTitle(e.target.value)} /></label>
        <label className="field"><span>Company</span>
          <input value={company} onChange={(e) => setCompany(e.target.value)} /></label>
        <label className="field"><span>Location</span>
          <input value={location} onChange={(e) => setLocation(e.target.value)} /></label>
      </div>
      <label className="field"><span>Description</span>
        <textarea rows={10} value={description} onChange={(e) => setDescription(e.target.value)} /></label>
      <div className="dialog-actions">
        <button type="submit" className="primary" disabled={busy}>{busy ? 'Saving…' : 'Save and score'}</button>
      </div>
      {error && <p className="error" role="alert">{error}</p>}
    </form>
  );
}
