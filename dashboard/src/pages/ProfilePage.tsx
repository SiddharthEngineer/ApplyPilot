import { useEffect, useState, type ChangeEvent, type ReactNode } from 'react';
import { Link } from 'react-router-dom';
import * as api from '../api';
import Flower from '../components/Flower';
import { Group, TagInput } from '../components/FormParts';
import { humanize } from '../labels';
import type { ProfileFields, ProfileResponse } from '../types';

type FieldKey = keyof ProfileFields;
type TextKey = { [K in FieldKey]: ProfileFields[K] extends string ? K : never }[FieldKey];

const isEmpty = (v: unknown) => v === null || v === '' || (Array.isArray(v) && v.length === 0);

/** The profile with `draft` filled into the fields that are still empty. */
function withDraft(form: ProfileFields, draft: Partial<ProfileFields>): ProfileFields {
  const next = { ...form };
  for (const [key, value] of Object.entries(draft) as [FieldKey, ProfileFields[FieldKey]][]) {
    if (value !== undefined && !isEmpty(value) && isEmpty(form[key])) (next as Record<FieldKey, unknown>)[key] = value;
  }
  return next;
}

export default function ProfilePage() {
  const [data, setData] = useState<ProfileResponse | null>(null);
  const [form, setForm] = useState<ProfileFields | null>(null);
  const [library, setLibrary] = useState('');
  const [saved, setSaved] = useState('');
  // The resume's draft (until Save or Discard); a field is highlighted while it still holds the drafted value.
  const [draft, setDraft] = useState<Partial<ProfileFields>>({});
  const [busy, setBusy] = useState(false);
  const [parsing, setParsing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [resumeNote, setResumeNote] = useState<string | null>(null);
  const [resumeError, setResumeError] = useState<string | null>(null);

  function load(res: ProfileResponse) {
    setData(res);
    setForm(res.profile);
    setLibrary(res.content_library_md);
    setSaved(JSON.stringify({ ...res.profile, content_library_md: res.content_library_md }));
  }

  useEffect(() => {
    const ctrl = new AbortController();
    api.getProfile(ctrl.signal).then(load).catch((err) => {
      if (!ctrl.signal.aborted) setError(err.message || String(err));
    });
    return () => ctrl.abort();
  }, []);

  if (error && !form) return <p className="notice error" role="alert">Couldn't load your profile: {error}</p>;
  if (!form || !data) {
    return (
      <div className="loader-state" aria-live="polite" aria-busy="true">
        <Flower variant="loader" size={64} />
        Loading profile…
      </div>
    );
  }

  const dirty = JSON.stringify({ ...form, content_library_md: library }) !== saved;
  const drafted = (Object.keys(draft) as FieldKey[]).filter(
    (k) => JSON.stringify(form[k]) === JSON.stringify(draft[k]));
  const set = <K extends FieldKey>(key: K, value: ProfileFields[K]) => setForm((f) => (f ? { ...f, [key]: value } : f));

  const row = (key: FieldKey, name: string, control: ReactNode) => (
    <div key={key} className={`pref-row${drafted.includes(key) ? ' drafted' : ''}`}>
      <div className="pref-label">{name}</div>
      <div className="pref-value">{control}</div>
      {drafted.includes(key) && <span className="tag tag-soon" title="From your resume; Save to keep it">From resume</span>}
    </div>
  );
  const textRow = (key: TextKey, name: string, type = 'text', placeholder?: string) => row(key, name, (
    <input type={type} aria-label={name} value={form[key]} placeholder={placeholder}
      onChange={(e) => set(key, e.target.value)} />
  ));

  async function onSave() {
    if (!form) return;
    setBusy(true);
    setError(null);
    setMessage(null);
    try {
      const res = await api.saveProfile({ ...form, content_library_md: library });
      load(res);
      setDraft({});
      setMessage('Saved.');
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  function onDiscard() {
    const old = JSON.parse(saved);
    setLibrary(old.content_library_md);
    delete old.content_library_md;
    setForm(old);
    setDraft({});
  }

  async function onUpload(e: ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    e.target.value = ''; // the same file can be picked again
    if (!file) return;
    setParsing(true);
    setResumeError(null);
    setResumeNote(null);
    try {
      const res = await api.uploadResume(file);
      setData((d) => (d ? { ...d, has_resume: res.has_resume, resume_file_name: res.resume_file_name } : d));
      setForm((f) => (f ? withDraft(f, res.draft) : f)); // fields typed in meanwhile stay as typed
      setDraft((d) => ({ ...d, ...res.draft }));
      if (res.parse_error) setResumeError(res.parse_error);
      else if (Object.keys(res.draft).length) setResumeNote('Resume saved. The highlighted fields are from it: check them, then Save.');
      else setResumeNote('Resume saved. Nothing new to fill in: your fields are already set.');
    } catch (err) {
      setResumeError(err instanceof Error ? err.message : String(err));
      // A 402 still stores the resume (it just isn't parsed).
      api.getProfile().then((res) => setData((d) => (d ? { ...d, has_resume: res.has_resume,
        resume_file_name: res.resume_file_name } : d))).catch(() => {});
    } finally {
      setParsing(false);
    }
  }

  return (
    <article className="prefs-page profile-page">
      <h1>Profile</h1>
      <p className="muted">
        Profile is about you; <Link to="/preferences">Preferences</Link> is about the job you want. Scoring reads your
        resume, and the resume workbench reads your content library and these details.
      </p>

      <Group title="About you">
        {textRow('first_name', 'First name')}
        {textRow('last_name', 'Last name')}
        {textRow('email', 'Email', 'email')}
        {textRow('phone', 'Phone', 'tel')}
        {textRow('linkedin_url', 'LinkedIn', 'url', 'https://www.linkedin.com/in/…')}
        {textRow('github_url', 'GitHub', 'url', 'https://github.com/…')}
        {textRow('location', 'Location', 'text', 'City, state')}
      </Group>

      <Group title="Experience">
        {textRow('current_job_title', 'Current title')}
        {row('years_experience', 'Years of experience', (
          <input type="number" min={0} max={70} step={0.5} aria-label="Years of experience"
            value={form.years_experience ?? ''} onChange={(e) => {
              const n = e.target.value.trim() === '' ? null : Number(e.target.value);
              set('years_experience', n !== null && Number.isFinite(n) && n >= 0 ? n : null);
            }} />
        ))}
        {row('education_level', 'Education', (
          <select aria-label="Education" value={form.education_level ?? ''}
            onChange={(e) => set('education_level', e.target.value || null)}>
            <option value="">Not set</option>
            {data.options.education_level.map((o) => <option key={o} value={o}>{humanize(o)}</option>)}
          </select>
        ))}
        {row('skills', 'Skills', (
          <TagInput label="Skills" values={form.skills} placeholder="e.g. Python; Enter to add"
            onChange={(v) => set('skills', v)} />
        ))}
      </Group>

      <Group title="Resume" hint="A PDF or DOCX, up to 5 MB. Uploading saves it right away (scoring reads it, so your
        jobs' assessments become stale), then Claude reads it (about $0.002) and fills in the fields above that are
        empty. Those are kept only when you Save.">
        <div className="pref-row">
          <div className="pref-label">Current file</div>
          <div className="pref-value">
            {data.resume_file_name
              ? <a href={api.RESUME_DOWNLOAD_URL}>{data.resume_file_name}</a>
              : <span className="muted">{data.has_resume ? 'A resume text, no file' : 'None yet'}</span>}
          </div>
        </div>
        <div className="pref-row">
          <div className="pref-label">Upload</div>
          <div className="pref-value">
            <input type="file" aria-label="Upload resume" disabled={parsing}
              accept=".pdf,.docx,application/pdf,application/vnd.openxmlformats-officedocument.wordprocessingml.document"
              onChange={onUpload} />
            {parsing && <span className="muted" role="status">Parsing…</span>}
            {resumeNote && <span className="ok-text" role="status">{resumeNote}</span>}
            {resumeError && <span className="error" role="alert">{resumeError}</span>}
          </div>
        </div>
      </Group>

      <Group title="Content library" hint="Your roles and projects in markdown: each role starts with a
        &quot;## CURRENT ROLE&quot; or &quot;## PRIOR ROLE&quot; heading. The resume workbench picks bullets from it.">
        <textarea aria-label="Content library" className="mono-area" rows={16} value={library}
          onChange={(e) => setLibrary(e.target.value)} />
        {data.content_library_error && <p className="error" role="alert">{data.content_library_error}</p>}
      </Group>

      <div className="save-bar">
        <button type="button" className="primary" disabled={!dirty || busy} onClick={onSave}>Save</button>
        {dirty && <button type="button" disabled={busy} onClick={onDiscard}>Discard</button>}
        {message && !dirty && <span className="ok-text" role="status">{message}</span>}
        {error && <span className="error" role="alert">{error}</span>}
      </div>
    </article>
  );
}
