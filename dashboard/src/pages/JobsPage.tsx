import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { getFacets, listJobs, rescoreAll, rescorePreview } from '../api';
import Filters from '../components/Filters';
import Flower from '../components/Flower';
import Hero from '../components/Hero';
import JobsTable from '../components/JobsTable';
import TaskProgress from '../components/TaskProgress';
import type { Facets, JobList, JobQuery, RescorePreview, Task } from '../types';
import { DEFAULT_SORT, PAGE_SIZE, useQueryState } from '../useQueryState';
import { usd } from './PreferencesPage';

const CLEAR_ALL_SOURCES: Partial<JobQuery> = {
  status: undefined, role: undefined, work_mode: undefined, location: undefined, found_from: undefined,
  found_to: undefined, due_from: undefined, due_to: undefined, score_min: undefined, score_max: undefined,
  q: undefined, hide_flagged: undefined, source: [],
};

export default function JobsPage({ panelDue = null }: { panelDue?: string | null }) {
  const [query, update] = useQueryState();
  const [data, setData] = useState<JobList | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [facets, setFacets] = useState<Facets | null>(null);
  const [facetsLoaded, setFacetsLoaded] = useState(false);
  const [reload, setReload] = useState(0);
  // Rescore all: the confirm dialog's preview, then the queued task.
  const [rescore, setRescore] = useState<RescorePreview | null>(null);
  const [rescoreBusy, setRescoreBusy] = useState(false);
  const [rescoreError, setRescoreError] = useState<string | null>(null);
  const [rescoreTask, setRescoreTask] = useState<Task | null>(null);
  // Without a source in the URL, show the jobs the user added when there are any.
  const hasManual = (facets?.source?.find((s) => s.value === 'manual')?.count ?? 0) > 0;
  const shown: JobQuery = query.source === undefined && hasManual ? { ...query, source: ['manual'] } : query;
  const queryKey = JSON.stringify(shown);

  useEffect(() => {
    const ctrl = new AbortController();
    getFacets(ctrl.signal).then(setFacets).catch(() => {}) // counts are optional
      .finally(() => { if (!ctrl.signal.aborted) setFacetsLoaded(true); });
    return () => ctrl.abort();
  }, []);

  useEffect(() => {
    if (query.source === undefined && !facetsLoaded) return; // the default source depends on the facets
    const ctrl = new AbortController();
    setLoading(true);
    setError(null);
    listJobs({ ...shown, page_size: PAGE_SIZE }, ctrl.signal)
      .then((res) => setData(res))
      .catch((err) => {
        if (!ctrl.signal.aborted) setError(err.message || String(err));
      })
      .finally(() => {
        if (!ctrl.signal.aborted) setLoading(false);
      });
    return () => ctrl.abort();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [queryKey, facetsLoaded, reload]);

  async function openRescore() {
    setRescoreBusy(true);
    setRescoreError(null);
    try {
      setRescore(await rescorePreview());
    } catch (err) {
      setRescoreError(err instanceof Error ? err.message : String(err));
    } finally {
      setRescoreBusy(false);
    }
  }

  function closeRescore() {
    setRescore(null);
    setRescoreError(null);
  }

  async function confirmRescore() {
    setRescoreBusy(true);
    setRescoreError(null);
    try {
      const res = await rescoreAll();
      setRescore(null);
      setRescoreTask(res.task);
    } catch (err) {
      setRescoreError(err instanceof Error ? err.message : String(err));
    } finally {
      setRescoreBusy(false);
    }
  }

  const sort = query.sort ?? DEFAULT_SORT;
  const page = query.page ?? 1;
  const total = data?.total ?? 0;
  const pages = Math.max(1, Math.ceil(total / PAGE_SIZE));
  const first = total ? (page - 1) * PAGE_SIZE + 1 : 0;
  const last = Math.min(page * PAGE_SIZE, total);

  return (
    <section>
      <Hero facets={facets} />
      <div className="list-header">
        <h1>Jobs</h1>
        {data && !error && (
          <span className="muted" data-testid="total">
            {total ? `${first.toLocaleString()}–${last.toLocaleString()} of ${total.toLocaleString()}` : '0 jobs'}
          </span>
        )}
        <button type="button" className="list-header-action" disabled={rescoreBusy} onClick={openRescore}>
          Rescore all
        </button>
      </div>
      {rescoreError && !rescore && <p className="notice error" role="alert">{rescoreError}</p>}
      {rescoreTask && (
        <TaskProgress task={rescoreTask} onFinish={(t) => {
          setRescoreTask(t);
          if (t.state === 'done') setReload((n) => n + 1);
        }} />
      )}
      {rescore && (
        <div className="dialog-backdrop">
          <div className="dialog" role="dialog" aria-modal="true" aria-labelledby="rescore-title">
            <h2 id="rescore-title">Rescore all jobs?</h2>
            {rescore.jobs ? (
              <p>
                Re-assess {rescore.jobs.toLocaleString()} active jobs with Claude, {usd(rescore.estimated_usd)}
                {' '}(takes up to an hour). Rejected jobs are left out.
              </p>
            ) : (
              <p>There are no active jobs to re-assess.</p>
            )}
            {rescore.skipped_unextracted > 0 && (
              <p className="muted">{rescore.skipped_unextracted.toLocaleString()} jobs skipped: not extracted yet.</p>
            )}
            <div className="dialog-actions">
              <button type="button" className="primary" disabled={rescoreBusy || !rescore.jobs}
                onClick={confirmRescore}>Re-assess</button>
              <button type="button" disabled={rescoreBusy} onClick={closeRescore}>Cancel</button>
            </div>
            {rescoreError && <p className="error" role="alert">{rescoreError}</p>}
          </div>
        </div>
      )}
      {panelDue && (
        <p className="stale-note panel-due" role="status">
          Panel due: {panelDue}. <Link to="/panel">Run the resume panel</Link>
        </p>
      )}
      <Filters query={shown} facets={facets}
        // Clear filters shows every source, or the "Added by me" default would come straight back.
        onChange={(patch) => update(patch === null && hasManual ? CLEAR_ALL_SOURCES : patch)} />
      {error ? (
        <p className="notice error" role="alert">Couldn't load jobs: {error}</p>
      ) : !data && loading ? (
        <div className="loader-state" aria-live="polite" aria-busy="true">
          <Flower variant="loader" size={64} />
          Loading jobs…
        </div>
      ) : data && data.items.length === 0 ? (
        <div className="empty-state">
          <Flower variant="seedling" size={64} />
          <p className="notice">No jobs match these filters.</p>
        </div>
      ) : data ? (
        <div className={loading ? 'loading' : undefined} aria-busy={loading}>
          <JobsTable items={data.items} sort={sort} onSort={(s) => update({ sort: s })} />
          <nav className="pager" aria-label="Pages">
            <button type="button" disabled={page <= 1 || loading} onClick={() => update({ page: page - 1 })}>
              ← Prev
            </button>
            <span>Page {page} of {pages}</span>
            <button type="button" disabled={page >= pages || loading} onClick={() => update({ page: page + 1 })}>
              Next →
            </button>
          </nav>
        </div>
      ) : null}
    </section>
  );
}
