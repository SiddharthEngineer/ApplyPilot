import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import * as api from '../api';
import { usd } from '../labels';
import PreferencesPage from '../pages/PreferencesPage';
import type { Preferences, PreferencesResponse, Task } from '../types';

vi.mock('../api');

// The server's defaults (scoring/preferences.py): synthetic, no real preferences.
function defaults(): Preferences {
  const dc = <V,>(value: V) => ({ importance: 'dont_care' as const, value });
  return {
    salary: { importance: 'important', value: { min: null, currency: 'USD', period: 'year' } },
    locations: { importance: 'important', value: { places: [], remote_ok: true } },
    work_mode: dc([]), role_category: { importance: 'important', value: [] }, seniority: dc([]),
    employment_type: dc([]), visa: dc(false), clearance: dc(false), travel: dc(null),
    red_flags_counted: ['mostly_sales', 'vague_posting'],
    skills_want: [], skills_avoid: [], industries_favor: [], industries_avoid: [], target_roles: [],
  };
}

const RESPONSE: PreferencesResponse = {
  prefs: defaults(),
  options: {
    work_mode: ['remote', 'hybrid', 'onsite'], role_category: ['data_science', 'data_engineering', 'other'],
    seniority: ['entry', 'mid', 'senior'], employment_type: ['full_time', 'contract'],
    salary_period: ['year', 'month', 'hour'],
    importance: ['dont_care', 'nice', 'important', 'very_important', 'dealbreaker'],
    roles_by_field: { data_engineering: ['Data Engineer', 'Analytics Engineer'], other: [] },
    red_flags: [{ value: 'mostly_sales', label: 'Mostly sales', help: 'the role is mainly selling despite its title' }],
  },
  labels: {
    salary: 'Salary', locations: 'Location', work_mode: 'Work mode', role_category: 'Job field', seniority: 'Seniority',
    employment_type: 'Employment type', visa: 'Visa sponsorship', clearance: 'Security clearance', travel: 'Travel',
    red_flags_counted: 'Red flags',
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
    expect(row('Travel')).toHaveClass('quiet');
    // Education, Experience, Companies to skip and the AI-judged weights are no longer preferences.
    for (const name of ['Skills', 'Education', 'Experience', 'Company']) {
      expect(screen.queryByText(name, { selector: '.pref-label' })).not.toBeInTheDocument();
    }
    for (const title of ['What the AI judges', 'Companies to skip', 'Role', 'Eligibility']) {
      expect(screen.queryByRole('heading', { name: title })).toBeNull();
    }
    expect(screen.getByRole('link', { name: 'Profile' })).toHaveAttribute('href', '/profile');
    // Basics holds the job field and target roles first; it and Skills and industries are open, the rest fold.
    const basics = screen.getByRole('heading', { name: 'Basics' }).closest('section') as HTMLElement;
    expect([...basics.querySelectorAll('.pref-label')].map((e) => e.textContent))
      .toEqual(['Job field', 'Target roles', 'Salary', 'Location', 'Work mode']);
    expect(screen.getByRole('heading', { name: 'Skills and industries' }).closest('section')).not.toBeNull();
    for (const title of ['Role requirements', 'Red flags']) {
      expect(screen.getByRole('heading', { name: title }).closest('details')).not.toHaveAttribute('open');
    }
    const requirements = screen.getByRole('heading', { name: 'Role requirements' }).closest('details') as HTMLElement;
    expect([...requirements.querySelectorAll('.pref-label')].map((e) => e.textContent))
      .toEqual(['Seniority', 'Employment type', 'Visa sponsorship', 'Security clearance', 'Travel']);
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
    await user.type(await screen.findByLabelText('Add other role'), 'Data Engineer{Enter}');
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
    // Choosing a field offers its roles.
    await user.click(within(screen.getByRole('group', { name: 'Data Engineering roles' }))
      .getByRole('button', { name: 'Analytics Engineer' }));
    await user.click(within(screen.getByRole('group', { name: 'Work modes' })).getByRole('button', { name: 'Remote' }));
    await user.click(screen.getByText('Role requirements'));
    await user.click(screen.getByRole('checkbox', { name: 'I need visa sponsorship' }));
    vi.mocked(api.previewPreferences).mockResolvedValue({ reassess_needed: false, jobs_to_reassess: 0, estimated_usd: 0 });
    await user.click(screen.getByRole('button', { name: 'Save' }));
    const sent = vi.mocked(api.previewPreferences).mock.calls[0][0];
    expect(sent.role_category.value).toEqual(['data_engineering']);
    expect(sent.target_roles).toEqual(['Analytics Engineer']);
    expect(sent.work_mode.value).toEqual(['remote']);
    expect(sent.visa.value).toBe(true);
  });

  it('red flags are checkboxes with a one-line explanation', async () => {
    const user = renderPage();
    await user.click(await screen.findByText('Red flags'));
    const flags = screen.getByRole('group', { name: 'Red flags' });
    const box = within(flags).getByRole('checkbox', { name: /Mostly sales/ });
    expect(box).toBeChecked();
    expect(flags).toHaveTextContent('the role is mainly selling despite its title');
    await user.click(box);
    vi.mocked(api.previewPreferences).mockResolvedValue({ reassess_needed: false, jobs_to_reassess: 0, estimated_usd: 0 });
    await user.click(screen.getByRole('button', { name: 'Save' }));
    expect(vi.mocked(api.previewPreferences).mock.calls[0][0].red_flags_counted).toEqual(['vague_posting']);
  });

  it('formats the estimate', () => {
    expect(usd(0.4)).toBe('about $0.40');
    expect(usd(12.345)).toBe('about $12.35');
    expect(usd(0.004)).toBe('under $0.01');
  });
});
