import { useEffect, useState, type ReactNode } from 'react';
import * as api from '../api';
import Flower from '../components/Flower';
import TaskProgress from '../components/TaskProgress';
import { humanize, IMPORTANCE_LABELS, IMPORTANCES, roleLabel, usd, WORK_MODE_LABELS } from '../labels';
import type {
  Criterion, Importance, Preferences, PreferencesResponse, ReassessPreview, ReassessScope, Task,
} from '../types';

type CriterionKey = { [K in keyof Preferences]: Preferences[K] extends Criterion ? K : never }[keyof Preferences];
type ListKey = 'skills_want' | 'skills_avoid' | 'industries_favor' | 'industries_avoid' | 'target_roles';

function ImportanceControl({ label, value, onChange }: {
  label: string; value: Importance; onChange: (v: Importance) => void;
}) {
  return (
    <div className="segmented small importance" role="group" aria-label={`${label} importance`}>
      {IMPORTANCES.map((imp) => (
        <button key={imp} type="button" aria-pressed={value === imp}
          className={imp === 'dealbreaker' ? 'dealbreaker' : undefined} onClick={() => onChange(imp)}>
          {IMPORTANCE_LABELS[imp]}
        </button>
      ))}
    </div>
  );
}

/** Toggle chips for a fixed list of values. */
function Chips({ label, options, selected, format = humanize, onChange }: {
  label: string; options: string[]; selected: string[]; format?: (v: string) => string;
  onChange: (v: string[]) => void;
}) {
  return (
    <div className="chips pick" role="group" aria-label={label}>
      {options.map((o) => {
        const on = selected.includes(o);
        return (
          <button key={o} type="button" className="chip-toggle" aria-pressed={on}
            onClick={() => onChange(on ? selected.filter((v) => v !== o) : [...selected, o])}>
            {format(o)}
          </button>
        );
      })}
    </div>
  );
}

/** Free-text tags: type and press Enter (or a comma) to add, × to remove. */
function TagInput({ label, values, placeholder, onChange }: {
  label: string; values: string[]; placeholder?: string; onChange: (v: string[]) => void;
}) {
  const [text, setText] = useState('');
  const add = () => {
    const parts = text.split(',').map((t) => t.trim()).filter((t) => t && !values.includes(t));
    if (parts.length) onChange([...values, ...parts]);
    setText('');
  };
  return (
    <div className="tag-input">
      {values.map((v) => (
        <span key={v} className="chip">
          {v}
          <button type="button" className="chip-remove" aria-label={`Remove ${v}`}
            onClick={() => onChange(values.filter((x) => x !== v))}>×</button>
        </span>
      ))}
      <input aria-label={label} value={text} placeholder={placeholder}
        onChange={(e) => setText(e.target.value)} onBlur={add}
        onKeyDown={(e) => {
          if (e.key === 'Enter' || e.key === ',') {
            e.preventDefault();
            add();
          }
        }} />
    </div>
  );
}

function numberOrNull(text: string): number | null {
  if (text.trim() === '') return null;
  const n = Number(text);
  return Number.isFinite(n) && n >= 0 ? n : null;
}

function Group({ title, hint, advanced = false, children }: {
  title: string; hint?: string; advanced?: boolean; children: ReactNode;
}) {
  const body = <>{hint && <p className="muted group-hint">{hint}</p>}<div className="pref-rows">{children}</div></>;
  if (advanced) {
    return (
      <details className="card pref-group">
        <summary><h2>{title}</h2></summary>
        {body}
      </details>
    );
  }
  return <section className="card pref-group"><h2>{title}</h2>{body}</section>;
}

export default function PreferencesPage() {
  const [data, setData] = useState<PreferencesResponse | null>(null);
  const [prefs, setPrefs] = useState<Preferences | null>(null);
  const [saved, setSaved] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [confirm, setConfirm] = useState<ReassessPreview | null>(null);
  const [scope, setScope] = useState<ReassessScope>('stale');
  const [task, setTask] = useState<Task | null>(null);

  useEffect(() => {
    const ctrl = new AbortController();
    api.getPreferences(ctrl.signal).then((res) => {
      setData(res);
      setPrefs(res.prefs);
      setSaved(JSON.stringify(res.prefs));
    }).catch((err) => {
      if (!ctrl.signal.aborted) setError(err.message || String(err));
    });
    return () => ctrl.abort();
  }, []);

  if (error && !prefs) return <p className="notice error" role="alert">Couldn't load your preferences: {error}</p>;
  if (!prefs || !data) {
    return (
      <div className="loader-state" aria-live="polite" aria-busy="true">
        <Flower variant="loader" size={64} />
        Loading preferences…
      </div>
    );
  }

  const label = (key: string) => data.labels[key] ?? humanize(key);
  const options = (key: string) => data.options[key] ?? [];
  const dirty = JSON.stringify(prefs) !== saved;

  function setCriterion<K extends CriterionKey>(key: K, patch: Partial<Preferences[K]>) {
    setPrefs((p) => (p ? { ...p, [key]: { ...p[key], ...patch } } : p));
  }
  const setValue = <K extends CriterionKey>(key: K, value: Preferences[K]['value']) =>
    setCriterion(key, { value } as Partial<Preferences[K]>);
  const setList = (key: ListKey, value: string[]) => setPrefs((p) => (p ? { ...p, [key]: value } : p));

  /** One criterion: label, value control (if any) and importance. Don't-care rows are visually quiet. */
  const row = (key: CriterionKey, control?: ReactNode, name = label(key)) => {
    const importance = prefs[key].importance;
    return (
      <div key={key} className={`pref-row${importance === 'dont_care' ? ' quiet' : ''}${
        importance === 'dealbreaker' ? ' dealbreaker' : ''}`}>
        <div className="pref-label">{name}</div>
        <div className="pref-value">{control}</div>
        <ImportanceControl label={name} value={importance}
          onChange={(v) => setCriterion(key, { importance: v } as Partial<Preferences[typeof key]>)} />
      </div>
    );
  };
  const listRow = (key: ListKey, name: string, placeholder: string) => (
    <div key={key} className="pref-row">
      <div className="pref-label">{name}</div>
      <div className="pref-value"><TagInput label={name} values={prefs[key]} placeholder={placeholder}
        onChange={(v) => setList(key, v)} /></div>
    </div>
  );

  async function save(confirmReassess: boolean) {
    if (!prefs) return;
    setBusy(true);
    setError(null);
    try {
      const res = await api.savePreferences(prefs, confirmReassess, scope);
      setPrefs(res.prefs);
      setSaved(JSON.stringify(res.prefs));
      setConfirm(null);
      setTask(res.task);
      setMessage(`Re-ranked ${res.rescored.toLocaleString()} jobs.${res.task ? ' Re-assessment queued.' : ''}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  /** The dialog's "Re-assess all active jobs" checkbox: re-fetch the count and cost for the chosen scope. */
  async function changeScope(next: ReassessScope) {
    if (!prefs) return;
    setScope(next);
    setBusy(true);
    setError(null);
    try {
      setConfirm(await api.previewPreferences(prefs, next));
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  async function onSave() {
    if (!prefs) return;
    setBusy(true);
    setError(null);
    setMessage(null);
    setScope('stale');
    try {
      const preview = await api.previewPreferences(prefs);
      if (preview.reassess_needed && preview.jobs_to_reassess > 0) {
        setConfirm(preview);
        setBusy(false);
        return;
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      setBusy(false);
      return;
    }
    await save(false);
  }

  const s = prefs.salary.value;
  const loc = prefs.locations.value;

  return (
    <article className="prefs-page">
      <h1>Preferences</h1>
      <p className="muted">
        Each job gets a fit score from 0 to 100. Set what matters and how much; a Dealbreaker hides the jobs that fail
        it (tick "Show flagged jobs" on the jobs list to see them). Changes here re-rank every job at once, for free.
        Only the inputs under "What the AI judges" need Claude to re-assess your jobs, and you'll see the cost first.
      </p>

      <Group title="Basics">
        {row('salary', (
          <div className="inline-inputs">
            <input type="number" min={0} step={1000} aria-label="Minimum salary" placeholder="Minimum"
              value={s.min ?? ''} onChange={(e) => setValue('salary', { ...s, min: numberOrNull(e.target.value) })} />
            <input aria-label="Currency" className="short" value={s.currency}
              onChange={(e) => setValue('salary', { ...s, currency: e.target.value.toUpperCase() })} />
            <select aria-label="Salary period" value={s.period}
              onChange={(e) => setValue('salary', { ...s, period: e.target.value })}>
              {options('salary_period').map((p) => <option key={p} value={p}>per {p}</option>)}
            </select>
          </div>
        ))}
        {row('locations', (
          <>
            <TagInput label="Places" values={loc.places} placeholder="City, state or country; Enter to add"
              onChange={(places) => setValue('locations', { ...loc, places })} />
            <label className="check">
              <input type="checkbox" checked={loc.remote_ok}
                onChange={(e) => setValue('locations', { ...loc, remote_ok: e.target.checked })} />
              Remote jobs count as a match
            </label>
          </>
        ))}
        {row('role_category', <Chips label="Job fields" options={options('role_category')}
          selected={prefs.role_category.value} format={roleLabel} onChange={(v) => setValue('role_category', v)} />)}
      </Group>

      <Group title="What the AI judges" hint="Claude rates each job on these against your resume. Changing the lists
        below makes every assessment stale; changing how much each one matters is free.">
        {listRow('skills_want', 'Skills I want to use', 'e.g. Python; Enter to add')}
        {listRow('skills_avoid', 'Skills I want to avoid', 'e.g. Excel macros')}
        {listRow('industries_favor', 'Industries I like', 'e.g. healthcare')}
        {listRow('industries_avoid', 'Industries to avoid', 'e.g. gambling')}
        {listRow('target_roles', 'Target roles', 'e.g. Data Engineer')}
        {row('skills_match')}
        {row('experience_match')}
        {row('domain_interest')}
        {row('red_flags')}
      </Group>

      <Group title="Role" advanced>
        {row('seniority', <Chips label="Seniority levels" options={options('seniority')}
          selected={prefs.seniority.value} onChange={(v) => setValue('seniority', v)} />)}
        {row('employment_type', <Chips label="Employment types" options={options('employment_type')}
          selected={prefs.employment_type.value} onChange={(v) => setValue('employment_type', v)} />)}
        {row('work_mode', <Chips label="Work modes" options={options('work_mode')} selected={prefs.work_mode.value}
          format={(m) => WORK_MODE_LABELS[m] ?? humanize(m)} onChange={(v) => setValue('work_mode', v)} />)}
        {row('experience', (
          <input type="number" min={0} step={1} aria-label="My years of experience" placeholder="My years"
            value={prefs.experience.value ?? ''} onChange={(e) => setValue('experience', numberOrNull(e.target.value))} />
        ))}
        {row('education', (
          <select aria-label="My education" value={prefs.education.value ?? ''}
            onChange={(e) => setValue('education', e.target.value || null)}>
            <option value="">Not set</option>
            {options('education').map((o) => <option key={o} value={o}>{humanize(o)}</option>)}
          </select>
        ))}
      </Group>

      <Group title="Eligibility" advanced>
        {row('visa', (
          <label className="check"><input type="checkbox" checked={prefs.visa.value}
            onChange={(e) => setValue('visa', e.target.checked)} />I need visa sponsorship</label>
        ))}
        {row('clearance', (
          <label className="check"><input type="checkbox" checked={prefs.clearance.value}
            onChange={(e) => setValue('clearance', e.target.checked)} />I hold a security clearance</label>
        ))}
        {row('travel', (
          <span className="inline-inputs">
            <input type="number" min={0} max={100} step={5} aria-label="Maximum travel" placeholder="Max"
              value={prefs.travel.value ?? ''} onChange={(e) => setValue('travel', numberOrNull(e.target.value))} />
            <span className="muted">% travel at most</span>
          </span>
        ))}
      </Group>

      <Group title="Companies to skip" advanced>
        {row('company_blocklist', <TagInput label="Companies" values={prefs.company_blocklist.value}
          placeholder="Company name; Enter to add" onChange={(v) => setValue('company_blocklist', v)} />)}
      </Group>

      <div className="save-bar">
        <button type="button" className="primary" disabled={!dirty || busy} onClick={onSave}>Save</button>
        {dirty && <button type="button" disabled={busy} onClick={() => setPrefs(JSON.parse(saved))}>Discard</button>}
        {message && <span className="ok-text" role="status">{message}</span>}
        {error && <span className="error" role="alert">{error}</span>}
      </div>
      {task && <TaskProgress task={task} onFinish={setTask} />}

      {confirm && (
        <div className="dialog-backdrop">
          <div className="dialog" role="dialog" aria-modal="true" aria-labelledby="reassess-title">
            <h2 id="reassess-title">Re-assess your jobs?</h2>
            <p>
              Re-assess {confirm.jobs_to_reassess.toLocaleString()} jobs with Claude, {usd(confirm.estimated_usd)}
              {' '}(takes up to an hour). Preference-only changes apply now for free.
            </p>
            {!!confirm.skipped_unextracted && (
              <p className="muted">{confirm.skipped_unextracted.toLocaleString()} jobs skipped: not extracted yet.</p>
            )}
            <label className="check">
              <input type="checkbox" checked={scope === 'active'} disabled={busy}
                onChange={(e) => changeScope(e.target.checked ? 'active' : 'stale')} />
              Re-assess all active jobs, not only the ones these changes affect
            </label>
            <div className="dialog-actions">
              <button type="button" className="primary" disabled={busy || !confirm.jobs_to_reassess}
                onClick={() => save(true)}>Re-assess</button>
              <button type="button" disabled={busy} onClick={() => save(false)}>Save without re-assessing</button>
              <button type="button" disabled={busy} onClick={() => setConfirm(null)}>Cancel</button>
            </div>
            {error && <p className="error" role="alert">{error}</p>}
          </div>
        </div>
      )}
    </article>
  );
}
