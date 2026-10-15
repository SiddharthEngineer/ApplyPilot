import { useEffect, useRef, useState, type FormEvent } from 'react';
import { useNavigate } from 'react-router-dom';
import { addJob } from '../api';
import { MIN_DESCRIPTION } from './PastePanel';

export const DUPLICATE_TOAST = 'You already had this job';

/** Add a job by link (or by pasting its description), then open its page. */
export default function AddJobDialog({ onClose }: { onClose: () => void }) {
  const navigate = useNavigate();
  const urlRef = useRef<HTMLInputElement>(null);
  const [url, setUrl] = useState('');
  const [title, setTitle] = useState('');
  const [company, setCompany] = useState('');
  const [location, setLocation] = useState('');
  const [description, setDescription] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    urlRef.current?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (!url.trim() && !description.trim()) {
      setError('Paste a link to the posting, or its description.');
      return;
    }
    if (!url.trim() && description.trim().length < MIN_DESCRIPTION) {
      setError(`Paste the whole description (at least ${MIN_DESCRIPTION} characters).`);
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const fields = { url: url.trim(), title: title.trim(), company: company.trim(), location: location.trim(),
        description: description.trim() };
      const input = Object.fromEntries(Object.entries(fields).filter(([, v]) => v));
      const res = await addJob(input);
      onClose();
      navigate(`/jobs/${res.key}`, { state: res.created ? null : { toast: DUPLICATE_TOAST } });
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      setBusy(false);
    }
  }

  return (
    <div className="dialog-backdrop" onClick={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <form className="dialog add-job" role="dialog" aria-modal="true" aria-labelledby="add-job-title" onSubmit={submit}>
        <h2 id="add-job-title">Add job</h2>
        <label className="field">
          <span>Link to the posting</span>
          <input ref={urlRef} type="text" inputMode="url" placeholder="https://…" value={url}
            onChange={(e) => setUrl(e.target.value)} />
        </label>
        <details className="paste-details">
          <summary>No link? Paste the description</summary>
          <div className="form-grid">
            <label className="field"><span>Title</span>
              <input value={title} onChange={(e) => setTitle(e.target.value)} /></label>
            <label className="field"><span>Company</span>
              <input value={company} onChange={(e) => setCompany(e.target.value)} /></label>
            <label className="field"><span>Location</span>
              <input value={location} onChange={(e) => setLocation(e.target.value)} /></label>
          </div>
          <label className="field"><span>Description</span>
            <textarea rows={8} value={description} onChange={(e) => setDescription(e.target.value)} /></label>
        </details>
        <p className="muted small">Strelitzia reads the posting, extracts it and scores your fit (about a minute).</p>
        <div className="dialog-actions">
          <button type="submit" className="primary" disabled={busy}>{busy ? 'Adding…' : 'Add job'}</button>
          <button type="button" disabled={busy} onClick={onClose}>Cancel</button>
        </div>
        {error && <p className="error" role="alert">{error}</p>}
      </form>
    </div>
  );
}
