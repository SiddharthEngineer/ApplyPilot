import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import * as api from '../api';
import App from '../App';
import { localInputToIso } from '../components/InterviewsPanel';
import ContactsPage from '../pages/ContactsPage';
import JobPage from '../pages/JobPage';
import JobsPage from '../pages/JobsPage';
import type { Task } from '../types';
import { contact, FACETS, interview, jobDetail, jobList, listItem } from './fixtures';

vi.mock('../api');

const LONG = 'We are hiring a data scientist to build forecasting models for our retail business. '.repeat(4);

function intakeTask(state: Task['state'], extra: Partial<Task> = {}): Task {
  return {
    id: 't9', url: 'https://boards.greenhouse.io/acme/jobs/1', kind: 'intake', state,
    created_at: '2026-10-15T10:00:00+00:00', started_at: state === 'queued' ? null : '2026-10-15T10:00:01+00:00',
    finished_at: state === 'done' || state === 'error' ? '2026-10-15T10:00:40+00:00' : null, error: null,
    key: 'abc123', ...extra,
  };
}

function LocationProbe() {
  const loc = useLocation();
  return <div data-testid="location">{loc.pathname + loc.search}</div>;
}

function renderAt(url: string, element = <App />) {
  return render(
    <MemoryRouter initialEntries={[url]}>
      {element}
      <LocationProbe />
    </MemoryRouter>,
  );
}

function renderJob() {
  return renderAt('/jobs/abc123', <Routes><Route path="/jobs/:key" element={<JobPage />} /></Routes>);
}

beforeEach(() => {
  vi.mocked(api.getJob).mockResolvedValue(jobDetail());
  vi.mocked(api.listJobTasks).mockResolvedValue([]);
  vi.mocked(api.getFacets).mockResolvedValue(FACETS);
  vi.mocked(api.listJobs).mockResolvedValue(jobList([listItem('active', 1)]));
  vi.mocked(api.listContacts).mockResolvedValue([]);
  vi.mocked(api.getPanel).mockResolvedValue({
    due: null, needed: 5, comparable: { latest_vs_previous: 0, rules_a_vs_b: 0 }, runs: [], project_name: 'Strelitzia Panel',
    project_instructions: '', rubric: { name: 'Strelitzia panel rubric', url: null, text: '' },
  });
});

afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
});

describe('Panel reminder', () => {
  it('marks the Panel nav link and the jobs list when a panel is due', async () => {
    vi.mocked(api.getPanel).mockResolvedValue({
      due: '31 days since the last panel', needed: 5, comparable: { latest_vs_previous: 6, rules_a_vs_b: 0 }, runs: [],
      project_name: 'Strelitzia Panel', project_instructions: '', rubric: { name: 'Strelitzia panel rubric', url: null, text: '' },
    });
    renderAt('/');
    const nav = screen.getByRole('navigation', { name: 'Main' });
    expect(await within(nav).findByRole('img', { name: 'due' })).toBeInTheDocument();
    const banner = await screen.findByText(/Panel due: 31 days since the last panel/);
    expect(within(banner).getByRole('link', { name: 'Run the resume panel' })).toHaveAttribute('href', '/panel');
  });

  it('shows nothing when no panel is due', async () => {
    renderAt('/');
    await screen.findByText('Job 1');
    expect(screen.queryByRole('img', { name: 'due' })).toBeNull();
    expect(screen.queryByText(/Panel due/)).toBeNull();
  });
});

describe('Add job', () => {
  it('submits a link and opens the new job', async () => {
    const user = userEvent.setup();
    vi.mocked(api.addJob).mockResolvedValue({ key: 'abc123', created: true, task: intakeTask('queued') });
    vi.mocked(api.getJob).mockResolvedValue(jobDetail({ source: 'manual', intake_status: 'queued', details: null }));
    vi.mocked(api.listJobTasks).mockResolvedValue([intakeTask('queued')]);
    renderAt('/');
    await user.click(screen.getByRole('button', { name: 'Add job' }));
    const dialog = screen.getByRole('dialog', { name: 'Add job' });
    expect(within(dialog).getByLabelText('Link to the posting')).toHaveFocus();
    await user.type(within(dialog).getByLabelText('Link to the posting'), 'https://boards.greenhouse.io/acme/jobs/1');
    await user.click(within(dialog).getByRole('button', { name: 'Add job' }));

    expect(api.addJob).toHaveBeenCalledWith({ url: 'https://boards.greenhouse.io/acme/jobs/1' });
    await waitFor(() => expect(screen.getByTestId('location')).toHaveTextContent('/jobs/abc123'));
    expect(screen.queryByRole('dialog')).toBeNull();
    expect(await screen.findByText(/Reading the posting/)).toBeInTheDocument();
    expect(screen.queryByText('You already had this job')).toBeNull();
  });

  it('a duplicate opens the existing job with a note', async () => {
    const user = userEvent.setup();
    vi.mocked(api.addJob).mockResolvedValue({ key: 'abc123', created: false, task: null });
    renderAt('/');
    await user.click(screen.getByRole('button', { name: 'Add job' }));
    await user.type(screen.getByLabelText('Link to the posting'), 'https://example.com/job/1');
    await user.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Add job' }));
    expect(await screen.findByText('You already had this job')).toBeInTheDocument();
    expect(screen.getByTestId('location')).toHaveTextContent('/jobs/abc123');
  });

  it('takes a pasted description without a link, and needs one or the other', async () => {
    const user = userEvent.setup();
    vi.mocked(api.addJob).mockResolvedValue({ key: 'abc123', created: true, task: intakeTask('queued') });
    renderAt('/');
    await user.click(screen.getByRole('button', { name: 'Add job' }));
    const dialog = screen.getByRole('dialog');
    await user.click(within(dialog).getByRole('button', { name: 'Add job' }));
    expect(within(dialog).getByRole('alert')).toHaveTextContent('Paste a link');
    expect(api.addJob).not.toHaveBeenCalled();

    await user.click(within(dialog).getByText('No link? Paste the description'));
    await user.type(within(dialog).getByLabelText('Description'), 'Too short');
    await user.click(within(dialog).getByRole('button', { name: 'Add job' }));
    expect(within(dialog).getByRole('alert')).toHaveTextContent('at least 200');
    expect(api.addJob).not.toHaveBeenCalled();
    await user.type(within(dialog).getByLabelText('Title'), 'Analyst');
    await user.type(within(dialog).getByLabelText('Company'), 'Globex');
    fireEvent.change(within(dialog).getByLabelText('Description'), { target: { value: LONG } });
    await user.click(within(dialog).getByRole('button', { name: 'Add job' }));
    expect(api.addJob).toHaveBeenCalledWith({ title: 'Analyst', company: 'Globex', description: LONG.trim() });
  });
});

describe('Intake on the job page', () => {
  it('polls the intake task, then shows the scored job', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    vi.mocked(api.getJob).mockResolvedValue(jobDetail({ intake_status: 'scraping', details: null, fit_score: null }));
    vi.mocked(api.listJobTasks).mockResolvedValue([intakeTask('running')]);
    vi.mocked(api.getTask).mockResolvedValue(intakeTask('done'));
    renderJob();
    expect(await screen.findByText(/Reading the posting/)).toBeInTheDocument();

    vi.mocked(api.getJob).mockResolvedValue(jobDetail({ intake_status: 'ok' }));
    await act(() => vi.advanceTimersByTimeAsync(3000));
    expect(await screen.findByText(/Read and scored the posting/)).toBeInTheDocument();
    await waitFor(() => expect(screen.getByRole('region', { name: 'Fit' })).toHaveTextContent('Fit score 80/100'));
  });

  it('shows the paste form on needs_paste and saves it', async () => {
    const user = userEvent.setup();
    vi.mocked(api.getJob).mockResolvedValue(jobDetail({
      url: 'https://www.linkedin.com/jobs/view/42/', title: null, company: 'Unknown company', details: null,
      fit_score: null, description_text: 'Sign in', intake_status: 'needs_paste',
      intake_error: 'The site showed a sign-in page instead of the posting.',
    }));
    vi.mocked(api.putDescription).mockResolvedValue({ task: intakeTask('queued'), key: 'abc123' });
    renderJob();
    const panel = await screen.findByRole('form', { name: "We couldn't read this posting" });
    expect(panel).toHaveTextContent('sign-in page');
    expect(within(panel).getByRole('link', { name: /Open posting/ })).toHaveAttribute('href', 'https://www.linkedin.com/jobs/view/42/');
    expect(within(panel).getByLabelText('Company')).toHaveValue('');
    expect(within(panel).getByLabelText('Description')).toHaveValue('Sign in');

    await user.click(within(panel).getByRole('button', { name: 'Save and score' }));
    expect(within(panel).getByRole('alert')).toHaveTextContent('at least 200');
    await user.type(within(panel).getByLabelText('Title'), 'ML Engineer');
    fireEvent.change(within(panel).getByLabelText('Description'), { target: { value: LONG } });
    vi.mocked(api.getJob).mockResolvedValue(jobDetail({ intake_status: 'queued', details: null }));
    await user.click(within(panel).getByRole('button', { name: 'Save and score' }));
    expect(api.putDescription).toHaveBeenCalledWith('abc123', {
      title: 'ML Engineer', location: 'Chicago, IL', description: LONG.trim(),
    });
    expect(await screen.findByText(/Reading the posting/)).toBeInTheDocument();
    expect(screen.queryByRole('form', { name: "We couldn't read this posting" })).toBeNull();
  });

  it('no paste form for a job that read fine', async () => {
    renderJob();
    await screen.findByRole('heading', { name: 'Data Scientist' });
    expect(screen.queryByText("We couldn't read this posting")).toBeNull();
  });
});

describe('Contacts panel', () => {
  it('creates a contact and links it, then edits its email on blur', async () => {
    const user = userEvent.setup();
    const ana = { ...contact({ id: 'new1', email: null, role: null }), relation: 'recruiter' as const };
    vi.mocked(api.linkContact).mockResolvedValue(jobDetail({ contacts: [ana] }));
    renderJob();
    await user.click(await screen.findByRole('button', { name: 'Add contact' }));
    await user.type(screen.getByLabelText('Find or create a contact'), 'Ana Ruiz');
    await waitFor(() => expect(api.listContacts).toHaveBeenLastCalledWith('Ana Ruiz', expect.anything()));
    await user.selectOptions(screen.getByLabelText('Relation'), 'recruiter');
    await user.click(screen.getByRole('button', { name: "Create 'Ana Ruiz'" }));
    expect(api.linkContact).toHaveBeenCalledWith('abc123', { contact: { name: 'Ana Ruiz', company: 'Acme' } }, 'recruiter');

    const section = screen.getByRole('region', { name: 'Contacts' });
    expect(within(section).getByText('Ana Ruiz')).toBeInTheDocument();
    expect(within(section).getByText('Recruiter')).toHaveClass('chip');
    vi.mocked(api.updateContact).mockResolvedValue({ ...ana, email: 'ana@acme.com', jobs: [] });
    vi.mocked(api.getJob).mockResolvedValue(jobDetail({ contacts: [{ ...ana, email: 'ana@acme.com' }] }));
    await user.type(within(section).getByLabelText('Email'), 'ana@acme.com'); // a new contact opens in edit mode
    await user.tab();
    expect(api.updateContact).toHaveBeenCalledWith('new1', { email: 'ana@acme.com' });
    expect(await within(section).findByRole('link', { name: 'ana@acme.com' })).toHaveAttribute('href', 'mailto:ana@acme.com');
  });

  it('links an existing contact found by search, and unlinks it', async () => {
    const user = userEvent.setup();
    vi.mocked(api.listContacts).mockResolvedValue([{ ...contact(), job_count: 1 }]);
    const linked = jobDetail({ contacts: [{ ...contact({ phone: '+1 (555) 0100' }), relation: 'referral' }] });
    vi.mocked(api.linkContact).mockResolvedValue(linked);
    vi.mocked(api.unlinkContact).mockResolvedValue(jobDetail());
    renderJob();
    await user.click(await screen.findByRole('button', { name: 'Add contact' }));
    await user.type(screen.getByLabelText('Find or create a contact'), 'an');
    await user.selectOptions(screen.getByLabelText('Relation'), 'referral');
    await user.click(await screen.findByRole('button', { name: /Ana Ruiz/ }));
    expect(api.linkContact).toHaveBeenCalledWith('abc123', { contact_id: 'c1' }, 'referral');
    expect(await screen.findByRole('link', { name: '+1 (555) 0100' })).toHaveAttribute('href', 'tel:+15550100');
    await user.click(screen.getByRole('button', { name: 'Remove Ana Ruiz' }));
    expect(api.unlinkContact).toHaveBeenCalledWith('abc123', 'c1');
    expect(await screen.findByText(/No contacts yet/)).toBeInTheDocument();
  });
});

describe('Interviews panel', () => {
  it('adds an interview from the inline form', async () => {
    const user = userEvent.setup();
    vi.mocked(api.createInterview).mockResolvedValue(interview());
    renderJob();
    await user.click(await screen.findByRole('button', { name: 'Add interview' }));
    const form = screen.getByRole('form', { name: 'Add interview' });
    fireEvent.change(within(form).getByLabelText('Date and time'), { target: { value: '2099-10-18T09:30' } });
    await user.type(within(form).getByLabelText('Stage'), 'Phone screen');
    await user.type(within(form).getByLabelText('Interviewers'), 'Ana');
    vi.mocked(api.getJob).mockResolvedValue(jobDetail({ interviews: [interview()] }));
    await user.click(within(form).getByRole('button', { name: 'Save interview' }));
    expect(api.createInterview).toHaveBeenCalledWith('abc123', {
      scheduled_at: localInputToIso('2099-10-18T09:30'), stage: 'Phone screen', interviewers: 'Ana',
    });
    expect(await screen.findByRole('listitem', { name: 'Phone screen · Video' })).toBeInTheDocument();
    expect(screen.queryByRole('form', { name: 'Add interview' })).toBeNull();
  });

  it('edits prep notes on blur and the outcome on change; past interviews collapse', async () => {
    const user = userEvent.setup();
    const past = interview({ id: 'i0', stage: 'Recruiter call', scheduled_at: '2020-01-01T10:00:00.000Z', outcome: 'passed' });
    vi.mocked(api.getJob).mockResolvedValue(jobDetail({ interviews: [past, interview()] }));
    vi.mocked(api.updateInterview).mockResolvedValue(interview());
    renderJob();
    const item = await screen.findByRole('listitem', { name: 'Phone screen · Video' });
    await user.type(within(item).getByLabelText('Prep notes'), 'Read the paper{Enter}Ask about the team');
    await user.tab();
    expect(api.updateInterview).toHaveBeenCalledWith('i1', { prep_notes: 'Read the paper\nAsk about the team' });
    await user.selectOptions(within(item).getByLabelText('Outcome'), 'passed');
    expect(api.updateInterview).toHaveBeenLastCalledWith('i1', { outcome: 'passed' });

    const details = screen.getByText('Past interviews (1)').closest('details')!;
    expect(details).not.toHaveAttribute('open');
    expect(within(details).getByRole('listitem', { name: 'Recruiter call · Video' })).toBeInTheDocument();
  });
});

describe('Jobs list: source and next interview', () => {
  it('defaults to "Added by me" when there are manual jobs, and "All sources" clears it', async () => {
    const user = userEvent.setup();
    vi.mocked(api.getFacets).mockResolvedValue({ ...FACETS, source: [{ value: 'manual', count: 2 }, { value: 'discovered', count: 8 }] });
    renderAt('/', <Routes><Route path="/" element={<JobsPage />} /></Routes>);
    await waitFor(() => expect(api.listJobs).toHaveBeenCalled());
    expect(vi.mocked(api.listJobs).mock.calls[0][0]).toMatchObject({ source: ['manual'] });
    const group = screen.getByRole('group', { name: 'Source' });
    expect(within(group).getByRole('button', { name: /Added by me/ })).toHaveAttribute('aria-pressed', 'true');

    await user.click(within(group).getByRole('button', { name: 'All sources' }));
    expect(screen.getByTestId('location')).toHaveTextContent('/?source=all');
    await waitFor(() => expect(vi.mocked(api.listJobs).mock.lastCall![0]).toMatchObject({ source: [] }));

    await user.click(within(group).getByRole('button', { name: /Discovered/ }));
    expect(screen.getByTestId('location')).toHaveTextContent('/?source=discovered');

    // Clear filters shows every source rather than falling back to the "Added by me" default.
    await user.click(screen.getByRole('button', { name: 'Clear filters' }));
    expect(screen.getByTestId('location')).toHaveTextContent('/?source=all');
    expect(within(group).getByRole('button', { name: 'All sources' })).toHaveAttribute('aria-pressed', 'true');
    expect(screen.getByRole('button', { name: 'Clear filters' })).toBeDisabled();
  });

  it('no manual jobs: no default source', async () => {
    renderAt('/', <Routes><Route path="/" element={<JobsPage />} /></Routes>);
    await waitFor(() => expect(api.listJobs).toHaveBeenCalled());
    expect(vi.mocked(api.listJobs).mock.calls[0]![0]?.source).toBeUndefined();
  });

  it('sorts by next interview soonest first and shows the badge within a week', async () => {
    const user = userEvent.setup();
    const soon = new Date(Date.now() + 2 * 86400_000).toISOString();
    vi.mocked(api.listJobs).mockResolvedValue(jobList([
      listItem('active', 1, { next_interview: soon }), listItem('active', 2),
    ]));
    renderAt('/', <Routes><Route path="/" element={<JobsPage />} /></Routes>);
    const rows = (await screen.findAllByRole('row')).slice(1);
    expect(within(rows[0]).getByText('Soon')).toBeInTheDocument();
    expect(within(rows[1]).queryByText('Soon')).toBeNull();
    await user.click(screen.getByRole('button', { name: /Next interview/ }));
    expect(screen.getByTestId('location')).toHaveTextContent('/?sort=next_interview');
    await user.click(screen.getByRole('button', { name: /Next interview/ }));
    expect(screen.getByTestId('location')).toHaveTextContent('/?sort=-next_interview');
  });
});

describe('Contacts page', () => {
  it('lists contacts, edits one and deletes it after a confirm', async () => {
    const user = userEvent.setup();
    vi.mocked(api.listContacts).mockResolvedValue([{ ...contact(), job_count: 2 }]);
    vi.mocked(api.getContact).mockResolvedValue({
      ...contact(), jobs: [{ key: 'abc123', title: 'Data Scientist', company: 'Acme', relation: 'recruiter' }],
    });
    vi.mocked(api.updateContact).mockResolvedValue({ ...contact({ phone: '555' }), jobs: [] });
    vi.mocked(api.deleteContact).mockResolvedValue({ ok: true });
    renderAt('/contacts', <Routes><Route path="/contacts" element={<ContactsPage />} /></Routes>);

    const row = (await screen.findAllByRole('row'))[1]!;
    expect(row).toHaveTextContent('Ana RuizAcmeRecruiterana@acme.com2');
    await user.click(within(row).getByRole('button', { name: 'Ana Ruiz' }));
    const editor = await screen.findByRole('region', { name: 'Edit Ana Ruiz' });
    expect(within(editor).getByRole('link', { name: 'Data Scientist' })).toHaveAttribute('href', '/jobs/abc123');
    await user.type(within(editor).getByLabelText('Phone'), '555');
    await user.tab();
    expect(api.updateContact).toHaveBeenCalledWith('c1', { phone: '555' });

    const confirm = vi.spyOn(window, 'confirm').mockReturnValueOnce(false).mockReturnValueOnce(true);
    await user.click(within(screen.getByRole('region', { name: /Edit/ })).getByRole('button', { name: 'Delete contact' }));
    expect(api.deleteContact).not.toHaveBeenCalled();
    await user.click(within(screen.getByRole('region', { name: /Edit/ })).getByRole('button', { name: 'Delete contact' }));
    expect(confirm).toHaveBeenCalledTimes(2);
    expect(api.deleteContact).toHaveBeenCalledWith('c1');
  });

  it('searches as you type', async () => {
    const user = userEvent.setup();
    renderAt('/contacts', <Routes><Route path="/contacts" element={<ContactsPage />} /></Routes>);
    await user.type(screen.getByLabelText('Search'), 'acme');
    await waitFor(() => expect(api.listContacts).toHaveBeenLastCalledWith('acme', expect.anything()));
    expect(await screen.findByText(/No contacts match this search/)).toBeInTheDocument();
  });
});
