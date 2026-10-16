import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import * as api from '../api';
import PreferencesPage, { usd } from '../pages/PreferencesPage';
import type { Preferences, PreferencesResponse, Task } from '../types';

vi.mock('../api');

// The server's defaults (scoring/preferences.py): synthetic, no real preferences.
function defaults(): Preferences {
  const dc = <V,>(value: V) => ({ importance: 'dont_care' as const, value });
  return {
    salary: { importance: 'important', value: { min: null, currency: 'USD', period: 'year' } },
    locations: { importance: 'important', value: { places: [], remote_ok: true } },
    work_mode: dc([]), role_category: { importance: 'important', value: [] }, seniority: dc([]),
    employment_type: dc([]), visa: dc(false), clearance: dc(false), travel: dc(null), education: dc(null),
    experience: dc(null), company_blocklist: dc([]),
    skills_match: { importance: 'very_important', value: null }, experience_match: dc(null),
    domain_interest: dc(null), red_flags: dc(null),
    skills_want: [], skills_avoid: [], industries_favor: [], industries_avoid: [], target_roles: [],
  };
}

const RESPONSE: PreferencesResponse = {
  prefs: defaults(),
  options: {
    work_mode: ['remote', 'hybrid', 'onsite'], role_category: ['data_science', 'data_engineering', 'other'],
    seniority: ['entry', 'mid', 'senior'], employment_type: ['full_time', 'contract'],
    education: ['bachelors', 'masters'], salary_period: ['year', 'month', 'hour'],
    importance: ['dont_care', 'nice', 'important', 'very_important', 'dealbreaker'], red_flags: ['mostly_sales'],
  },
  labels: {
    salary: 'Salary', locations: 'Location', work_mode: 'Work mode', role_category: 'Job field', seniority: 'Seniority',
    employment_type: 'Employment type', visa: 'Visa sponsorship', clearance: 'Security clearance', travel: 'Travel',
    education: 'Education', experience: 'Experience', company_blocklist: 'Company', skills_match: 'Skills',
    experience_match: 'Experience match', domain_interest: 'Domain interest', red_flags: 'Red flags',
  },
};

const reassessTask: Task = {
  id: 't9', url: null, kind: 'reassess_all', state: 'queued', created_at: '2026-10-13T12:00:00+00:00',
  started_at: null, finished_at: null, error: null, key: null,
};

function renderPage() {
  render(<MemoryRouter><PreferencesPage /></MemoryRouter>);
  return userEvent.setup();
}

const row = (name: string) => screen.getByText(name, { selector: '.pref-label' }).closest('.pref-row') as HTMLElement;
const importance = (name: string) => screen.getByRole('group', { name: `${name} importance` });

beforeEach(() => {
  vi.mocked(api.getPreferences).mockResolvedValue(structuredClone(RESPONSE));
  vi.mocked(api.savePreferences).mockImplementation(async (prefs) => ({ prefs, rescored: 412, task: null }));
});

describe('PreferencesPage', () => {
  it('shows the defaults: salary, location and job field Important, everything else quiet', async () => {
    renderPage();
    expect(await screen.findByRole('heading', { name: 'Preferences' })).toBeInTheDocument();
    for (const name of ['Salary', 'Location', 'Job field']) {
      expect(within(importance(name)).getByRole('button', { name: 'Important' })).toHaveAttribute('aria-pressed', 'true');
      expect(row(name)).not.toHaveClass('quiet');
    }
    expect(within(importance('Skills')).getByRole('button', { name: 'Very important' }))
      .toHaveAttribute('aria-pressed', 'true');
    expect(row('Red flags')).toHaveClass('quiet');
    // Advanced groups start collapsed.
    for (const title of ['Role', 'Eligibility', 'Companies to skip']) {
      expect(screen.getByRole('heading', { name: title }).closest('details')).not.toHaveAttribute('open');
    }
    expect(screen.getByRole('button', { name: 'Save' })).toBeDisabled(); // nothing changed yet
  });

  it('a preference-only change saves straight away and reports the re-rank', async () => {
    vi.mocked(api.previewPreferences).mockResolvedValue({ reassess_needed: false, jobs_to_reassess: 0, estimated_usd: 0 });
    const user = renderPage();
    await user.type(await screen.findByLabelText('Minimum salary'), '120000');
    await user.click(within(importance('Salary')).getByRole('button', { name: 'Dealbreaker' }));
    expect(row('Salary')).toHaveClass('dealbreaker');
    await user.click(screen.getByRole('button', { name: 'Save' }));

    const sent = vi.mocked(api.previewPreferences).mock.calls[0][0];
    expect(sent.salary).toEqual({ importance: 'dealbreaker', value: { min: 120000, currency: 'USD', period: 'year' } });
    expect(api.savePreferences).toHaveBeenCalledWith(sent, false, 'stale');
    expect(screen.queryByRole('dialog')).toBeNull();
    expect(await screen.findByRole('status')).toHaveTextContent('Re-ranked 412 jobs.');
    expect(screen.getByRole('button', { name: 'Save' })).toBeDisabled();
  });

  it('an input Claude judges asks for confirmation with the job count and cost', async () => {
    vi.mocked(api.previewPreferences).mockResolvedValue({ reassess_needed: true, jobs_to_reassess: 412, estimated_usd: 0.4 });
    vi.mocked(api.savePreferences).mockImplementation(async (prefs) => ({ prefs, rescored: 412, task: reassessTask }));
    const user = renderPage();
    await user.type(await screen.findByLabelText('Skills I want to use'), 'Rust{Enter}');
    expect(screen.getByRole('button', { name: 'Remove Rust' })).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Save' }));

    const dialog = await screen.findByRole('dialog');
    expect(dialog).toHaveTextContent('Re-assess 412 jobs with Claude, about $0.40 (takes up to an hour). '
      + 'Preference-only changes apply now for free.');
    expect(api.savePreferences).not.toHaveBeenCalled();
    await user.click(within(dialog).getByRole('button', { name: 'Re-assess' }));
    expect(vi.mocked(api.savePreferences).mock.calls[0][0].skills_want).toEqual(['Rust']);
    expect(vi.mocked(api.savePreferences).mock.calls[0][1]).toBe(true);
    expect(vi.mocked(api.savePreferences).mock.calls[0][2]).toBe('stale');
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
    expect(screen.getByText(/Re-ranked 412 jobs. Re-assessment queued./)).toBeInTheDocument();
    expect(screen.getByText(/Queued: re-assessment of your jobs/)).toBeInTheDocument();
  });

  it('the dialog checkbox re-assesses every active job, with its own count and cost', async () => {
    vi.mocked(api.previewPreferences).mockImplementation(async (_prefs, scope) => (scope === 'active'
      ? { reassess_needed: true, jobs_to_reassess: 900, estimated_usd: 1.2, skipped_unextracted: 3 }
      : { reassess_needed: true, jobs_to_reassess: 412, estimated_usd: 0.4 }));
    const user = renderPage();
    await user.type(await screen.findByLabelText('Skills I want to use'), 'Rust{Enter}');
    await user.click(screen.getByRole('button', { name: 'Save' }));
    const dialog = await screen.findByRole('dialog');
    const box = within(dialog).getByRole('checkbox', { name: /Re-assess all active jobs, not only the ones these/ });
    expect(box).not.toBeChecked();
    await user.click(box);
    expect(vi.mocked(api.previewPreferences).mock.calls.at(-1)?.[1]).toBe('active');
    await waitFor(() => expect(dialog).toHaveTextContent('Re-assess 900 jobs with Claude, about $1.20'));
    expect(dialog).toHaveTextContent('3 jobs skipped: not extracted yet');
    await user.click(within(dialog).getByRole('button', { name: 'Re-assess' }));
    expect(vi.mocked(api.savePreferences).mock.calls[0].slice(1)).toEqual([true, 'active']);
  });

  it('can save without re-assessing, or cancel', async () => {
    vi.mocked(api.previewPreferences).mockResolvedValue({ reassess_needed: true, jobs_to_reassess: 3, estimated_usd: 0.001 });
    const user = renderPage();
    await user.type(await screen.findByLabelText('Target roles'), 'Data Engineer{Enter}');
    await user.click(screen.getByRole('button', { name: 'Save' }));
    expect(await screen.findByRole('dialog')).toHaveTextContent('under $0.01');
    await user.click(screen.getByRole('button', { name: 'Cancel' }));
    expect(screen.queryByRole('dialog')).toBeNull();
    expect(api.savePreferences).not.toHaveBeenCalled();

    await user.click(screen.getByRole('button', { name: 'Save' }));
    await user.click(await screen.findByRole('button', { name: 'Save without re-assessing' }));
    expect(api.savePreferences).toHaveBeenCalledWith(
      expect.objectContaining({ target_roles: ['Data Engineer'] }), false, 'stale');
  });

  it('a budget stop keeps the dialog open with the message', async () => {
    vi.mocked(api.previewPreferences).mockResolvedValue({ reassess_needed: true, jobs_to_reassess: 9, estimated_usd: 2 });
    vi.mocked(api.savePreferences).mockRejectedValue(new Error('Monthly LLM budget reached: $4.99 spent'));
    const user = renderPage();
    await user.type(await screen.findByLabelText('Industries to avoid'), 'Gambling{Enter}');
    await user.click(screen.getByRole('button', { name: 'Save' }));
    await user.click(await screen.findByRole('button', { name: 'Re-assess' }));
    expect(await within(screen.getByRole('dialog')).findByRole('alert')).toHaveTextContent('budget reached');
    expect(screen.getByRole('button', { name: 'Save' })).toBeEnabled(); // still unsaved
  });

  it('chips and toggles edit set values', async () => {
    const user = renderPage();
    await user.click(await screen.findByRole('button', { name: 'Data Engineering' }));
    await user.click(screen.getByText('Role'));
    await user.click(within(screen.getByRole('group', { name: 'Work modes' })).getByRole('button', { name: 'Remote' }));
    await user.click(screen.getByText('Eligibility'));
    await user.click(screen.getByRole('checkbox', { name: 'I need visa sponsorship' }));
    vi.mocked(api.previewPreferences).mockResolvedValue({ reassess_needed: false, jobs_to_reassess: 0, estimated_usd: 0 });
    await user.click(screen.getByRole('button', { name: 'Save' }));
    const sent = vi.mocked(api.previewPreferences).mock.calls[0][0];
    expect(sent.role_category.value).toEqual(['data_engineering']);
    expect(sent.work_mode.value).toEqual(['remote']);
    expect(sent.visa.value).toBe(true);
  });

  it('formats the estimate', () => {
    expect(usd(0.4)).toBe('about $0.40');
    expect(usd(12.345)).toBe('about $12.35');
    expect(usd(0.004)).toBe('under $0.01');
  });
});
