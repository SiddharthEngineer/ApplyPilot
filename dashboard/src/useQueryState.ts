// The jobs page's filters, sort and page, kept in the URL query string so reloads and shared links keep them.
import { useCallback, useMemo } from 'react';
import { useSearchParams } from 'react-router-dom';
import { STATUS_FILTERS } from './labels';
import type { JobQuery, Sort, StatusFilter } from './types';

export const DEFAULT_SORT: Sort = '-discovered_at';
export const PAGE_SIZE = 50;

const SORT_RE = /^-?(discovered_at|deadline|fit_score)$/;
const DATE_RE = /^\d{4}-\d{2}-\d{2}$/;
const DATE_KEYS = ['found_from', 'found_to', 'due_from', 'due_to'] as const;

function intParam(value: string | null, min: number, max: number): number | undefined {
  if (value === null || value.trim() === '') return undefined;
  const n = Number(value);
  return Number.isInteger(n) && n >= min && n <= max ? n : undefined;
}

/** URL params -> query, dropping anything invalid. */
export function parseQuery(params: URLSearchParams): JobQuery {
  const q: JobQuery = {};
  const status = params.getAll('status').filter((s): s is StatusFilter => STATUS_FILTERS.includes(s as StatusFilter));
  if (status.length && !status.includes('all')) q.status = status;
  const role = params.getAll('role').filter(Boolean);
  if (role.length) q.role = role;
  const workMode = params.getAll('work_mode').filter(Boolean);
  if (workMode.length) q.work_mode = workMode;
  const location = params.get('location');
  if (location) q.location = location;
  for (const key of DATE_KEYS) {
    const value = params.get(key);
    if (value && DATE_RE.test(value)) q[key] = value;
  }
  const scoreMin = intParam(params.get('score_min'), 0, 10);
  if (scoreMin !== undefined) q.score_min = scoreMin;
  const scoreMax = intParam(params.get('score_max'), 0, 10);
  if (scoreMax !== undefined) q.score_max = scoreMax;
  const text = params.get('q');
  if (text) q.q = text;
  const sort = params.get('sort');
  if (sort && SORT_RE.test(sort) && sort !== DEFAULT_SORT) q.sort = sort as Sort;
  const page = intParam(params.get('page'), 1, 1_000_000);
  if (page && page > 1) q.page = page;
  return q;
}

/** Query -> URL params, leaving out empty values and defaults. */
export function toSearchParams(q: JobQuery): URLSearchParams {
  const params = new URLSearchParams();
  for (const [name, value] of Object.entries(q)) {
    if (value === undefined || value === null || value === '') continue;
    if (name === 'sort' && value === DEFAULT_SORT) continue;
    if (name === 'page' && value === 1) continue;
    if (name === 'page_size') continue;
    if (Array.isArray(value)) value.forEach((v) => params.append(name, String(v)));
    else params.set(name, String(value));
  }
  return params;
}

/** The current query and a setter that merges a patch. Any change except a page change goes back to page 1. */
export function useQueryState(): [JobQuery, (patch: Partial<JobQuery> | null) => void] {
  const [params, setParams] = useSearchParams();
  const query = useMemo(() => parseQuery(params), [params]);
  const update = useCallback(
    (patch: Partial<JobQuery> | null) => {
      setParams(
        (prev) => {
          if (patch === null) {
            // Clear filters but keep the sort.
            const sort = parseQuery(prev).sort;
            return toSearchParams(sort ? { sort } : {});
          }
          const next: JobQuery = { ...parseQuery(prev), ...patch };
          if (!('page' in patch)) delete next.page;
          return toSearchParams(next);
        },
        { replace: false },
      );
    },
    [setParams],
  );
  return [query, update];
}
