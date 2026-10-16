import { afterEach, describe, expect, it, vi } from 'vitest';
import {
  addJob, ApiError, jobsQueryString, linkContact, listJobs, onUnauthorized, openPacket, pasteResult, putDescription,
  rescore, savePreferences, setStatus,
} from '../api';
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
    mockFetch(422, { detail: "submitted_at can't be cleared; set a status instead" });
    const err = await setStatus('abc', 'submitted').catch((e) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect(err.status).toBe(422);
    expect(err.message).toBe("submitted_at can't be cleared; set a status instead");
  });

  it('POSTs the packet request with the CSRF header', async () => {
    const fetch = mockFetch(200, { prompt: 'p', doc_url: null, inline: true, rules_version: 'abcd1234' });
    expect((await openPacket('k1')).inline).toBe(true);
    const [url, init] = fetch.mock.calls[0];
    expect(url).toBe('/app/api/jobs/k1/packet');
    expect(init.method).toBe('POST');
    expect(init.headers['X-Strelitzia']).toBe('1');
  });

  it('returns a failed paste as errors and a fix message instead of throwing', async () => {
    const fetch = mockFetch(422, { detail: 'bad', errors: ['bad', 'worse'], warnings: [], fix_prompt: 'These problems…' });
    const out = await pasteResult('k1', 'reply text');
    expect(out).toEqual({ ok: false, errors: ['bad', 'worse'], warnings: [], fix_prompt: 'These problems…' });
    const [url, init] = fetch.mock.calls[0];
    expect(url).toBe('/app/api/jobs/k1/result');
    expect(init.headers['X-Strelitzia']).toBe('1');
    expect(JSON.parse(init.body)).toEqual({ text: 'reply text' });
  });

  it('returns a saved paste with the job, and throws other errors', async () => {
    mockFetch(200, { outcome: { draft_version: 2, warnings: [], coverage: null, unmet: [] }, job: { key: 'k1' } });
    const out = await pasteResult('k1', 'reply');
    expect(out.ok && out.outcome.draft_version).toBe(2);

    mockFetch(409, { detail: 'No content library' });
    const err = await pasteResult('k1', 'reply').catch((e) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect(err.message).toBe('No content library');
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

  it('sends the source filter and the next-interview sort', () => {
    expect(jobsQueryString({ source: ['manual'], sort: 'next_interview' })).toBe('source=manual&sort=next_interview');
    expect(jobsQueryString({ source: [], sort: '-next_interview' })).toBe('sort=-next_interview'); // all sources
  });

  it('POSTs a new job and PUTs a pasted description with the CSRF header', async () => {
    const fetch = mockFetch(201, { key: 'k1', created: true, task: null });
    await addJob({ url: 'https://example.com/1' });
    let [url, init] = fetch.mock.calls[0];
    expect(url).toBe('/app/api/jobs');
    expect(init.method).toBe('POST');
    expect(init.headers['X-Strelitzia']).toBe('1');
    expect(JSON.parse(init.body)).toEqual({ url: 'https://example.com/1' });

    const put = mockFetch(200, { task: null, key: 'k1' });
    await putDescription('k1', { description: 'text' });
    [url, init] = put.mock.calls[0];
    expect(url).toBe('/app/api/jobs/k1/description');
    expect(init.method).toBe('PUT');
  });

  it('links a contact with its relation', async () => {
    const fetch = mockFetch(200, {});
    await linkContact('k1', { contact: { name: 'Ana' } }, 'recruiter');
    const [url, init] = fetch.mock.calls[0];
    expect(url).toBe('/app/api/jobs/k1/contacts');
    expect(JSON.parse(init.body)).toEqual({ contact: { name: 'Ana' }, relation: 'recruiter' });
  });
});

describe('401 handling', () => {
  afterEach(() => onUnauthorized(null));

  it('tells the app when a request comes back 401, and still throws', async () => {
    const listener = vi.fn();
    onUnauthorized(listener);
    mockFetch(401, { detail: 'Not signed in' });
    await expect(listJobs()).rejects.toMatchObject({ status: 401, message: 'Not signed in' });
    expect(listener).toHaveBeenCalledTimes(1);
  });

  it('leaves other errors alone', async () => {
    const listener = vi.fn();
    onUnauthorized(listener);
    mockFetch(403, { detail: 'Only the owner can manage users' });
    await expect(listJobs()).rejects.toBeInstanceOf(ApiError);
    expect(listener).not.toHaveBeenCalled();
  });
});
