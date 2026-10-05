import { afterEach, describe, expect, it, vi } from 'vitest';
import { ApiError, generate, jobsQueryString, listJobs, setStatus } from '../api';

function mockFetch(status: number, body: unknown) {
  const fn = vi.fn().mockResolvedValue(
    new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } }),
  );
  vi.stubGlobal('fetch', fn);
  return fn;
}

afterEach(() => vi.unstubAllGlobals());

describe('api', () => {
  it('builds repeated list params and skips empty values', () => {
    expect(jobsQueryString({ status: ['active', 'submitted'], location: '', q: 'data', score_min: 5 })).toBe(
      'status=active&status=submitted&q=data&score_min=5',
    );
  });

  it('GETs jobs without the CSRF header', async () => {
    const fetch = mockFetch(200, { total: 0, page: 1, page_size: 50, items: [] });
    await listJobs({ sort: '-fit_score' });
    const [url, init] = fetch.mock.calls[0];
    expect(url).toBe('/app/api/jobs?sort=-fit_score');
    expect(init.headers['X-ApplyPilot']).toBeUndefined();
  });

  it('sends X-ApplyPilot and JSON on mutating calls', async () => {
    const fetch = mockFetch(200, {});
    await setStatus('abc', 'submitted', '2026-10-01');
    const [url, init] = fetch.mock.calls[0];
    expect(url).toBe('/app/api/jobs/abc/status');
    expect(init.method).toBe('POST');
    expect(init.headers['X-ApplyPilot']).toBe('1');
    expect(JSON.parse(init.body)).toEqual({ status: 'submitted', submitted_at: '2026-10-01' });
  });

  it('throws ApiError with the server detail', async () => {
    mockFetch(422, { detail: 'Choose a resume, a cover letter, or both' });
    const err = await generate('abc', { resume: false, cover: false }).catch((e) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect(err.status).toBe(422);
    expect(err.message).toBe('Choose a resume, a cover letter, or both');
  });
});
