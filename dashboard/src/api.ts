// Typed fetch helpers for /app/api. Mutating calls send `X-Strelitzia: 1` (the server's CSRF check).
import type {
  AddJobInput, AddJobResult, AdminUser, AuthMe, ClaudeSetup, Contact, ContactFields, ContactListItem, ContactWithJobs,
  Facets, Interview, ImportResult, InterviewFields, JobDetail, JobList, JobQuery, PacketResult, PanelGradeOutcome,
  PanelMode, PanelOverview, PanelRun, PanelWinner, PasteOutcome, Preferences, PreferencesResponse, ReassessPreview,
  Relation, SavePreferencesResult, Task, UserStatus,
} from './types';

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
    if (typeof detail?.message === 'string') return detail.message; // e.g. the 402 budget stop
    if (Array.isArray(detail)) return detail.map((d) => d?.msg ?? String(d)).join('; ');
  } catch {
    // not JSON
  }
  return `${res.status} ${res.statusText}`.trim();
}

type UnauthorizedListener = () => void;
let unauthorized: UnauthorizedListener | null = null;

/** Call `listener` whenever a request comes back 401 (the session ended): the app shows the sign-in screen. */
export function onUnauthorized(listener: UnauthorizedListener | null): void {
  unauthorized = listener;
}

async function failure(res: Response): Promise<ApiError> {
  if (res.status === 401) unauthorized?.();
  return new ApiError(res.status, await errorMessage(res));
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const method = init?.method ?? 'GET';
  const headers: Record<string, string> = { Accept: 'application/json' };
  if (method !== 'GET') {
    headers['X-Strelitzia'] = '1';
    if (init?.body !== undefined) headers['Content-Type'] = 'application/json';
  }
  const res = await fetch(API_BASE + path, { ...init, headers });
  if (!res.ok) throw await failure(res);
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

/** Write the job's Claude packet (a Google Doc when Drive is set up) and get the prompt to paste in Claude. */
export function openPacket(key: string): Promise<PacketResult> {
  return request<PacketResult>(`/jobs/${encodeURIComponent(key)}/packet`, { method: 'POST' });
}

/** Check and save a pasted Claude reply: ok with the saved draft, or not ok with the errors and a fix message. */
export async function pasteResult(key: string, text: string): Promise<PasteOutcome> {
  const res = await fetch(`${API_BASE}/jobs/${encodeURIComponent(key)}/result`, {
    method: 'POST',
    headers: { Accept: 'application/json', 'Content-Type': 'application/json', 'X-Strelitzia': '1' },
    body: JSON.stringify({ text }),
  });
  if (res.status === 422) {
    const body = await res.clone().json().catch(() => null);
    if (body && Array.isArray(body.errors)) {
      return { ok: false, errors: body.errors, warnings: body.warnings ?? [], fix_prompt: body.fix_prompt ?? '' };
    }
  }
  if (!res.ok) throw await failure(res);
  return { ok: true, ...((await res.json()) as ImportResult) };
}

export function getClaudeSetup(signal?: AbortSignal): Promise<ClaudeSetup> {
  return request<ClaudeSetup>('/claude/setup', { signal });
}

/** Write (or refresh) the rules and content-library Docs in Drive. */
export function publishClaudeDocs(): Promise<ClaudeSetup> {
  return request<ClaudeSetup>('/claude/docs', { method: 'POST' });
}

/** Forget the user's Google Drive token (their files stay in their Drive). */
export function disconnectDrive(): Promise<{ ok: boolean }> {
  return request<{ ok: boolean }>('/drive/disconnect', { method: 'POST' });
}

/** Copy text to the clipboard (with a fallback for browsers without the async clipboard API). */
export async function copyText(text: string): Promise<void> {
  if (navigator.clipboard?.writeText) {
    await navigator.clipboard.writeText(text);
    return;
  }
  const area = document.createElement('textarea');
  area.value = text;
  document.body.appendChild(area);
  area.select();
  try {
    if (!document.execCommand('copy')) throw new Error('Copy failed; select the text and copy it by hand.');
  } finally {
    area.remove();
  }
}

export function getTask(id: string, signal?: AbortSignal): Promise<Task> {
  return request<Task>(`/tasks/${encodeURIComponent(id)}`, { signal });
}

export function listJobTasks(key: string, signal?: AbortSignal): Promise<Task[]> {
  return request<Task[]>(`/jobs/${encodeURIComponent(key)}/tasks`, { signal });
}

/** Queue a fresh Claude assessment of one job. Poll getTask; a 402 means the monthly budget is used up. */
export function rescore(key: string): Promise<Task> {
  return request<Task>(`/jobs/${encodeURIComponent(key)}/rescore`, { method: 'POST' });
}

export function getPreferences(signal?: AbortSignal): Promise<PreferencesResponse> {
  return request<PreferencesResponse>('/preferences', { signal });
}

/** What saving `prefs` would re-assess and roughly cost. Saves nothing. */
export function previewPreferences(prefs: Preferences): Promise<ReassessPreview> {
  return request<ReassessPreview>('/preferences/preview', { method: 'POST', body: JSON.stringify({ prefs }) });
}

/** Save and re-rank now; with confirmReassess, also queue re-assessment of the jobs it makes stale. */
export function savePreferences(prefs: Preferences, confirmReassess = false): Promise<SavePreferencesResult> {
  return request<SavePreferencesResult>('/preferences', {
    method: 'PUT',
    body: JSON.stringify({ prefs, confirm_reassess: confirmReassess }),
  });
}

const job = (key: string) => `/jobs/${encodeURIComponent(key)}`;

/** Add a job by link (or by pasted description). An existing link returns created: false and the same key. */
export function addJob(input: AddJobInput): Promise<AddJobResult> {
  return request<AddJobResult>('/jobs', { method: 'POST', body: JSON.stringify(input) });
}

/** Save a pasted description for a posting that couldn't be read; queues extraction and scoring. */
export function putDescription(
  key: string, fields: { title?: string; company?: string; location?: string; description: string },
): Promise<{ task: Task; key: string }> {
  return request(`${job(key)}/description`, { method: 'PUT', body: JSON.stringify(fields) });
}

export function listContacts(q?: string, signal?: AbortSignal): Promise<ContactListItem[]> {
  return request<ContactListItem[]>(`/contacts${q ? `?q=${encodeURIComponent(q)}` : ''}`, { signal });
}

export function getContact(id: string, signal?: AbortSignal): Promise<ContactWithJobs> {
  return request<ContactWithJobs>(`/contacts/${encodeURIComponent(id)}`, { signal });
}

export function createContact(fields: ContactFields & { name: string }): Promise<Contact> {
  return request<Contact>('/contacts', { method: 'POST', body: JSON.stringify(fields) });
}

export function updateContact(id: string, fields: ContactFields): Promise<ContactWithJobs> {
  return request<ContactWithJobs>(`/contacts/${encodeURIComponent(id)}`, { method: 'PATCH', body: JSON.stringify(fields) });
}

export function deleteContact(id: string): Promise<{ ok: boolean }> {
  return request(`/contacts/${encodeURIComponent(id)}`, { method: 'DELETE' });
}

/** Link an existing contact (`contact_id`) or a new one (`contact`) to a job. Returns the updated job. */
export function linkContact(
  key: string, link: { contact_id: string } | { contact: ContactFields & { name: string } }, relation: Relation,
): Promise<JobDetail> {
  return request<JobDetail>(`${job(key)}/contacts`, { method: 'POST', body: JSON.stringify({ ...link, relation }) });
}

export function unlinkContact(key: string, contactId: string): Promise<JobDetail> {
  return request<JobDetail>(`${job(key)}/contacts/${encodeURIComponent(contactId)}`, { method: 'DELETE' });
}

export function createInterview(key: string, fields: InterviewFields): Promise<Interview> {
  return request<Interview>(`${job(key)}/interviews`, { method: 'POST', body: JSON.stringify(fields) });
}

export function updateInterview(id: string, fields: InterviewFields): Promise<Interview> {
  return request<Interview>(`/interviews/${encodeURIComponent(id)}`, { method: 'PATCH', body: JSON.stringify(fields) });
}

export function deleteInterview(id: string): Promise<{ ok: boolean }> {
  return request(`/interviews/${encodeURIComponent(id)}`, { method: 'DELETE' });
}

export function getPanel(signal?: AbortSignal): Promise<PanelOverview> {
  return request<PanelOverview>('/panel', { signal });
}

/** Start a blind panel: n jobs with two comparable drafts each. 409 when there are none. */
export function createPanelRun(n: number, mode: PanelMode): Promise<PanelRun> {
  return request<PanelRun>('/panel/runs', { method: 'POST', body: JSON.stringify({ n, mode }) });
}

export function getPanelRun(id: string, signal?: AbortSignal): Promise<PanelRun> {
  return request<PanelRun>(`/panel/runs/${encodeURIComponent(id)}`, { signal });
}

async function postPanelGrade(id: string, body: object): Promise<PanelGradeOutcome> {
  const res = await fetch(`${API_BASE}/panel/runs/${encodeURIComponent(id)}/grades`, {
    method: 'POST',
    headers: { Accept: 'application/json', 'Content-Type': 'application/json', 'X-Strelitzia': '1' },
    body: JSON.stringify(body),
  });
  if (res.status === 422) {
    const err = await res.clone().json().catch(() => null);
    if (err && Array.isArray(err.errors)) return { ok: false, errors: err.errors };
  }
  if (!res.ok) throw await failure(res);
  return { ok: true, ...((await res.json()) as { graded: number | null; run: PanelRun }) };
}

/** Import a model's pasted reply: ok with the run, or not ok with the errors (e.g. the wrong item count). */
export function importPanelGrades(id: string, grader: 'sonnet' | 'opus', text: string): Promise<PanelGradeOutcome> {
  return postPanelGrade(id, { grader, text });
}

/** Your blind pick for one item, in packet 1's order. */
export async function pickPanelItem(id: string, item: number, winner: PanelWinner): Promise<PanelRun> {
  const out = await postPanelGrade(id, { grader: 'you', item, winner });
  if (!out.ok) throw new ApiError(422, out.errors[0]);
  return out.run;
}

/** Whether sign-in is on and who is signed in (never a 401: user is null without a session). */
export function getMe(signal?: AbortSignal): Promise<AuthMe> {
  return request<AuthMe>('/auth/me', { signal });
}

/** The Google sign-in page (a full-page redirect through the server). */
export const LOGIN_URL = `${API_BASE}/auth/login`;

export function logout(): Promise<{ ok: boolean }> {
  return request<{ ok: boolean }>('/auth/logout', { method: 'POST' });
}

// --- The owner's Users page (owner-only: 403 for everyone else) ---

export async function listUsers(signal?: AbortSignal): Promise<AdminUser[]> {
  return (await request<{ users: AdminUser[] }>('/admin/users', { signal })).users;
}

/** Invite an email; without a cap the server's default applies. 409 when the email already has an account. */
export function inviteUser(email: string, monthlyCapUsd?: number | null): Promise<AdminUser> {
  const body: Record<string, string | number | null> = { email };
  if (monthlyCapUsd !== undefined) body.monthly_cap_usd = monthlyCapUsd;
  return request<AdminUser>('/admin/users', { method: 'POST', body: JSON.stringify(body) });
}

/** Change a user's cap (null = no cap) or re-enable them. */
export function updateUser(
  id: string, patch: { status?: 'active' | 'disabled'; monthly_cap_usd?: number | null },
): Promise<AdminUser> {
  return request<AdminUser>(`/admin/users/${encodeURIComponent(id)}`, { method: 'PATCH', body: JSON.stringify(patch) });
}

/** Disable a user: they can't sign in, and their data is kept. */
export function disableUser(id: string): Promise<AdminUser> {
  return request<AdminUser>(`/admin/users/${encodeURIComponent(id)}`, { method: 'DELETE' });
}
