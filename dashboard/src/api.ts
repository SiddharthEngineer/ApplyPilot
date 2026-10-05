// Typed fetch helpers for /app/api. Mutating calls send `X-ApplyPilot: 1` (the server's CSRF check).
import type { Facets, JobDetail, JobList, JobQuery, Task, UserStatus } from './types';

export const API_BASE = '/app/api';

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
    this.name = 'ApiError';
  }
}

async function errorMessage(res: Response): Promise<string> {
  try {
    const body = await res.json();
    const detail = body?.detail;
    if (typeof detail === 'string') return detail;
    if (Array.isArray(detail)) return detail.map((d) => d?.msg ?? String(d)).join('; ');
  } catch {
    // not JSON
  }
  return `${res.status} ${res.statusText}`.trim();
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const method = init?.method ?? 'GET';
  const headers: Record<string, string> = { Accept: 'application/json' };
  if (method !== 'GET') {
    headers['X-ApplyPilot'] = '1';
    if (init?.body !== undefined) headers['Content-Type'] = 'application/json';
  }
  const res = await fetch(API_BASE + path, { ...init, headers });
  if (!res.ok) throw new ApiError(res.status, await errorMessage(res));
  return (await res.json()) as T;
}

/** The query string for a jobs request (list params repeat: status=a&status=b). Empty values are left out. */
export function jobsQueryString(query: JobQuery): string {
  const params = new URLSearchParams();
  for (const [name, value] of Object.entries(query)) {
    if (value === undefined || value === null || value === '') continue;
    if (Array.isArray(value)) value.forEach((v) => params.append(name, String(v)));
    else params.set(name, String(value));
  }
  return params.toString();
}

export function listJobs(query: JobQuery = {}, signal?: AbortSignal): Promise<JobList> {
  const qs = jobsQueryString(query);
  return request<JobList>(`/jobs${qs ? `?${qs}` : ''}`, { signal });
}

export function getFacets(signal?: AbortSignal): Promise<Facets> {
  return request<Facets>('/facets', { signal });
}

export function getJob(key: string, signal?: AbortSignal): Promise<JobDetail> {
  return request<JobDetail>(`/jobs/${encodeURIComponent(key)}`, { signal });
}

/** Set (or with null, reset) the user's status. submittedAt is YYYY-MM-DD; the server defaults it to today. */
export function setStatus(key: string, status: UserStatus | null, submittedAt?: string): Promise<JobDetail> {
  const body: Record<string, string | null> = { status };
  if (submittedAt) body.submitted_at = submittedAt;
  return request<JobDetail>(`/jobs/${encodeURIComponent(key)}/status`, {
    method: 'POST',
    body: JSON.stringify(body),
  });
}

export function patchJob(key: string, patch: { submitted_at?: string; notes?: string | null }): Promise<JobDetail> {
  return request<JobDetail>(`/jobs/${encodeURIComponent(key)}`, { method: 'PATCH', body: JSON.stringify(patch) });
}

export function generate(key: string, what: { resume: boolean; cover: boolean }): Promise<Task> {
  return request<Task>(`/jobs/${encodeURIComponent(key)}/generate`, { method: 'POST', body: JSON.stringify(what) });
}

export function getTask(id: string, signal?: AbortSignal): Promise<Task> {
  return request<Task>(`/tasks/${encodeURIComponent(id)}`, { signal });
}

export function listJobTasks(key: string, signal?: AbortSignal): Promise<Task[]> {
  return request<Task[]>(`/jobs/${encodeURIComponent(key)}/tasks`, { signal });
}
