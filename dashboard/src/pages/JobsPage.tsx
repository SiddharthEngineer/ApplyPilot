import { useEffect, useState } from 'react';
import { listJobs } from '../api';
import JobsTable from '../components/JobsTable';
import type { JobList } from '../types';
import { DEFAULT_SORT, PAGE_SIZE, useQueryState } from '../useQueryState';

export default function JobsPage() {
  const [query, update] = useQueryState();
  const [data, setData] = useState<JobList | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const queryKey = JSON.stringify(query);

  useEffect(() => {
    const ctrl = new AbortController();
    setLoading(true);
    setError(null);
    listJobs({ ...query, page_size: PAGE_SIZE }, ctrl.signal)
      .then((res) => setData(res))
      .catch((err) => {
        if (!ctrl.signal.aborted) setError(err.message || String(err));
      })
      .finally(() => {
        if (!ctrl.signal.aborted) setLoading(false);
      });
    return () => ctrl.abort();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [queryKey]);

  const sort = query.sort ?? DEFAULT_SORT;
  const page = query.page ?? 1;
  const total = data?.total ?? 0;
  const pages = Math.max(1, Math.ceil(total / PAGE_SIZE));
  const first = total ? (page - 1) * PAGE_SIZE + 1 : 0;
  const last = Math.min(page * PAGE_SIZE, total);

  return (
    <section>
      <div className="list-header">
        <h1>Jobs</h1>
        {data && !error && (
          <span className="muted" data-testid="total">
            {total ? `${first.toLocaleString()}–${last.toLocaleString()} of ${total.toLocaleString()}` : '0 jobs'}
          </span>
        )}
      </div>
      {error ? (
        <p className="notice error" role="alert">Couldn't load jobs: {error}</p>
      ) : !data && loading ? (
        <p className="notice">Loading jobs…</p>
      ) : data && data.items.length === 0 ? (
        <p className="notice">No jobs match these filters.</p>
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
