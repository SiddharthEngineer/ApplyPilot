import { afterEach, describe, expect, it, vi } from 'vitest';
import { ApiError, generate, jobsQueryString, listJobs, rescore, savePreferences, setStatus } from '../api';
import type { Preferences } from '../types';

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
    expect(init.headers['X-Strelitzia']).toBeUndefined();
  });

  it('sends X-Strelitzia and JSON on mutating calls', async () => {
    const fetch = mockFetch(200, {});
    await setStatus('abc', 'submitted', '2026-10-01');
    const [url, init] = fetch.mock.calls[0];
    expect(url).toBe('/app/api/jobs/abc/status');
    expect(init.method).toBe('POST');
    expect(init.headers['X-Strelitzia']).toBe('1');
    expect(JSON.parse(init.body)).toEqual({ status: 'submitted', submitted_at: '2026-10-01' });
  });

  it('throws ApiError with the server detail', async () => {
    mockFetch(422, { detail: 'Choose a resume, a cover letter, or both' });
    const err = await generate('abc', { resume: false, cover: false }).catch((e) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect(err.status).toBe(422);
    expect(err.message).toBe('Choose a resume, a cover letter, or both');
  });

  it('reads the message of a structured error (the 402 budget stop)', async () => {
    mockFetch(402, { detail: { message: 'Monthly LLM budget reached', month_spend: 4.99, estimated_usd: 0.4 } });
    const err = await rescore('abc').catch((e) => e);
    expect(err.status).toBe(402);
    expect(err.message).toBe('Monthly LLM budget reached');
  });

  it('PUTs preferences with the confirmation flag', async () => {
    const fetch = mockFetch(200, { prefs: {}, rescored: 0, task: null });
    await savePreferences({ skills_want: ['Rust'] } as unknown as Preferences, true);
    const [url, init] = fetch.mock.calls[0];
    expect(url).toBe('/app/api/preferences');
    expect(init.method).toBe('PUT');
    expect(JSON.parse(init.body)).toEqual({ prefs: { skills_want: ['Rust'] }, confirm_reassess: true });
  });

  it('sends hide_flagged=false when flagged jobs are shown', () => {
    expect(jobsQueryString({ hide_flagged: false, score_min: 70 })).toBe('hide_flagged=false&score_min=70');
  });
});
