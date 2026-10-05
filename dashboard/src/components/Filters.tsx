import { useEffect, useRef, useState } from 'react';
import { ROLE_LABELS, roleLabel, STATUS_FILTERS, STATUS_LABELS, WORK_MODE_LABELS } from '../labels';
import type { Facets, JobQuery, StatusFilter } from '../types';

export const DEBOUNCE_MS = 300;
const WORK_MODES = ['remote', 'hybrid', 'onsite'] as const;
const SCORES = Array.from({ length: 10 }, (_, i) => i + 1);

interface Props {
  query: JobQuery;
  facets: Facets | null;
  onChange: (patch: Partial<JobQuery> | null) => void;
}

/** A text input that reports its value DEBOUNCE_MS after typing stops, and follows outside changes (e.g. Clear). */
function DebouncedInput({ value, onCommit, ...rest }: { value: string; onCommit: (v: string) => void } & Omit<
  React.InputHTMLAttributes<HTMLInputElement>, 'value' | 'onChange'>) {
  const [text, setText] = useState(value);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(() => {
    if (!timer.current) setText(value);
  }, [value]);
  useEffect(() => () => { if (timer.current) clearTimeout(timer.current); }, []);
  return (
    <input
      {...rest}
      value={text}
      onChange={(e) => {
        const next = e.target.value;
        setText(next);
        if (timer.current) clearTimeout(timer.current);
        timer.current = setTimeout(() => {
          timer.current = null;
          onCommit(next.trim());
        }, DEBOUNCE_MS);
      }}
    />
  );
}

function toggle<T>(list: T[] | undefined, value: T): T[] | undefined {
  const current = list ?? [];
  const next = current.includes(value) ? current.filter((v) => v !== value) : [...current, value];
  return next.length ? next : undefined;
}

export default function Filters({ query, facets, onChange }: Props) {
  const statusCounts = new Map(facets?.status.map((s) => [s.value, s.count]));
  const roleCounts = new Map(facets?.role_category.map((r) => [r.value, r.count]));
  const current: StatusFilter = query.status?.[0] ?? 'all';
  const roles = Object.keys(ROLE_LABELS);
  const selectedRoles = query.role ?? [];
  const hasFilters = Object.keys(query).some((k) => k !== 'sort' && k !== 'page');

  return (
    <div className="filters">
      <div className="segmented" role="group" aria-label="Status">
        {STATUS_FILTERS.map((s) => {
          const count = s === 'all' ? facets?.total : statusCounts.get(s);
          return (
            <button
              key={s}
              type="button"
              aria-pressed={current === s}
              onClick={() => onChange({ status: s === 'all' ? undefined : [s] })}
            >
              {s === 'all' ? 'All' : STATUS_LABELS[s]}
              {count !== undefined && <span className="count">{count.toLocaleString()}</span>}
            </button>
          );
        })}
      </div>

      <div className="filter-row">
        <label className="field grow">
          <span>Search</span>
          <DebouncedInput
            type="search"
            placeholder="Title or company"
            value={query.q ?? ''}
            onCommit={(v) => onChange({ q: v || undefined })}
          />
        </label>

        <details className="dropdown field">
          <summary>
            Role{selectedRoles.length ? ` (${selectedRoles.length})` : ''}
          </summary>
          <div className="dropdown-panel" role="group" aria-label="Role category">
            {roles.map((r) => (
              <label key={r} className="check">
                <input
                  type="checkbox"
                  checked={selectedRoles.includes(r)}
                  onChange={() => onChange({ role: toggle(query.role, r) })}
                />
                {roleLabel(r)}
                <span className="count">{(roleCounts.get(r) ?? 0).toLocaleString()}</span>
              </label>
            ))}
          </div>
        </details>

        <label className="field">
          <span>Location</span>
          <DebouncedInput
            list="location-options"
            placeholder="City, state or Remote"
            value={query.location ?? ''}
            onCommit={(v) => onChange({ location: v || undefined })}
          />
          <datalist id="location-options">
            {facets?.location.map((l) => <option key={l.value} value={l.value} />)}
          </datalist>
        </label>

        <div className="segmented small" role="group" aria-label="Work mode">
          {WORK_MODES.map((m) => (
            <button
              key={m}
              type="button"
              aria-pressed={query.work_mode?.includes(m) ?? false}
              onClick={() => onChange({ work_mode: toggle(query.work_mode, m) })}
            >
              {WORK_MODE_LABELS[m]}
            </button>
          ))}
        </div>
      </div>

      <div className="filter-row">
        <fieldset className="range">
          <legend>Found</legend>
          <input type="date" aria-label="Found from" value={query.found_from ?? ''}
            onChange={(e) => onChange({ found_from: e.target.value || undefined })} />
          <span>–</span>
          <input type="date" aria-label="Found to" value={query.found_to ?? ''}
            onChange={(e) => onChange({ found_to: e.target.value || undefined })} />
        </fieldset>
        <fieldset className="range">
          <legend>Due</legend>
          <input type="date" aria-label="Due from" value={query.due_from ?? ''}
            onChange={(e) => onChange({ due_from: e.target.value || undefined })} />
          <span>–</span>
          <input type="date" aria-label="Due to" value={query.due_to ?? ''}
            onChange={(e) => onChange({ due_to: e.target.value || undefined })} />
        </fieldset>
        <fieldset className="range">
          <legend>Fit score</legend>
          <select aria-label="Fit score min" value={query.score_min ?? ''}
            onChange={(e) => onChange({ score_min: e.target.value ? Number(e.target.value) : undefined })}>
            <option value="">Any</option>
            {SCORES.map((n) => <option key={n} value={n}>{n}</option>)}
          </select>
          <span>–</span>
          <select aria-label="Fit score max" value={query.score_max ?? ''}
            onChange={(e) => onChange({ score_max: e.target.value ? Number(e.target.value) : undefined })}>
            <option value="">Any</option>
            {SCORES.map((n) => <option key={n} value={n}>{n}</option>)}
          </select>
        </fieldset>
        <button type="button" className="clear" disabled={!hasFilters} onClick={() => onChange(null)}>
          Clear filters
        </button>
      </div>
    </div>
  );
}
