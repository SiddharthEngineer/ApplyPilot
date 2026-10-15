import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import * as api from '../api';
import { todayIso } from '../labels';
import JobPage, { compensation } from '../pages/JobPage';
import type { Task } from '../types';
import { jobDetail } from './fixtures';

vi.mock('../api');

function task(state: Task['state'], extra: Partial<Task> = {}): Task {
  return {
    id: 't1', url: 'https://example.com/job/1', kind: 'both', state, created_at: '2026-10-05T20:00:00+00:00',
    started_at: state === 'queued' ? null : '2026-10-05T20:00:01+00:00',
    finished_at: state === 'done' || state === 'error' ? '2026-10-05T20:00:30+00:00' : null,
    error: state === 'error' ? 'The Claude API is overloaded right now; try again in a few minutes.' : null,
    key: 'abc123', ...extra,
  };
}

function renderJob() {
  return render(
    <MemoryRouter initialEntries={['/jobs/abc123']}>
      <Routes>
        <Route path="/jobs/:key" element={<JobPage />} />
      </Routes>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  vi.mocked(api.getJob).mockResolvedValue(jobDetail());
  vi.mocked(api.listJobTasks).mockResolvedValue([]);
});

afterEach(() => {
  vi.useRealTimers();
});

describe('JobPage sections', () => {
  it('renders the extracted details', async () => {
    renderJob();
    expect(await screen.findByRole('heading', { name: 'Data Scientist' })).toBeInTheDocument();
    for (const title of ['Summary', 'Responsibilities', 'Required qualifications', 'Preferred qualifications', 'Skills',
      'Compensation', 'Location and work mode', 'Seniority and type', 'Education and experience',
      'Sponsorship and clearance', 'Benefits', 'Team', 'Status history']) {
      expect(screen.getByRole('heading', { name: title })).toBeInTheDocument();
    }
    expect(screen.getByText('$120,000 – $150,000 per year')).toBeInTheDocument();
    expect(screen.getByText('Spark')).toHaveClass('chip-preferred');
    expect(screen.getByText('3+ years')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: /Posting/ })).toHaveAttribute('href', 'https://example.com/job/1');
    expect(screen.queryByRole('link', { name: /Drive folder/ })).toBeNull(); // missing links are hidden
    expect(screen.queryByText(/Not extracted yet/)).toBeNull();
  });

  it('falls back to the raw description without details', async () => {
    vi.mocked(api.getJob).mockResolvedValue(jobDetail({ details: null }));
    renderJob();
    expect(await screen.findByText(/Not extracted yet/)).toBeInTheDocument();
    expect(screen.getByText('We are hiring a data scientist to build models.')).toBeInTheDocument();
    expect(screen.queryByRole('heading', { name: 'Responsibilities' })).toBeNull();
  });

  it('formats compensation ranges', () => {
    const d = jobDetail().details!;
    expect(compensation({ ...d, salary_min: 45, salary_max: null, salary_period: 'hour' })).toBe('from $45 per hour');
    expect(compensation({ ...d, salary_min: null, salary_max: null })).toBeNull();
  });
});

describe('JobPage status actions', () => {
  it('marks submitted with today by default', async () => {
    const user = userEvent.setup();
    vi.mocked(api.setStatus).mockResolvedValue(jobDetail({ status: 'submitted', status_color: 'blue', submitted_date: todayIso() }));
    renderJob();
    await user.click(await screen.findByRole('button', { name: 'Mark submitted' }));
    expect(screen.getByLabelText('Submitted on')).toHaveValue(todayIso());
    await user.click(screen.getByRole('button', { name: 'Save' }));
    expect(api.setStatus).toHaveBeenCalledWith('abc123', 'submitted', todayIso());
    expect(await screen.findByLabelText('Submitted date')).toHaveValue(todayIso());
  });

  it('marks submitted with an edited date, then edits it inline', async () => {
    const user = userEvent.setup();
    vi.mocked(api.setStatus).mockResolvedValue(jobDetail({ status: 'submitted', status_color: 'blue', submitted_date: '2026-09-28' }));
    vi.mocked(api.patchJob).mockResolvedValue(jobDetail({ status: 'submitted', status_color: 'blue', submitted_date: '2026-09-27' }));
    renderJob();
    await user.click(await screen.findByRole('button', { name: 'Mark submitted' }));
    const input = screen.getByLabelText('Submitted on');
    await user.clear(input);
    await user.type(input, '2026-09-28');
    await user.click(screen.getByRole('button', { name: 'Save' }));
    expect(api.setStatus).toHaveBeenCalledWith('abc123', 'submitted', '2026-09-28');

    // A date picker reports one complete value.
    fireEvent.change(await screen.findByLabelText('Submitted date'), { target: { value: '2026-09-27' } });
    await waitFor(() => expect(api.patchJob).toHaveBeenLastCalledWith('abc123', { submitted_at: '2026-09-27' }));
  });

  it('rejected, heard back and reset call the status endpoint', async () => {
    const user = userEvent.setup();
    vi.mocked(api.setStatus).mockResolvedValue(jobDetail({ status: 'in_progress', status_color: 'yellow' }));
    renderJob();
    await user.click(await screen.findByRole('button', { name: 'Rejected' }));
    expect(api.setStatus).toHaveBeenLastCalledWith('abc123', 'rejected', undefined);
    await user.click(screen.getByRole('button', { name: 'Heard back' }));
    expect(api.setStatus).toHaveBeenLastCalledWith('abc123', 'heard_back', undefined);
    await user.click(screen.getByRole('button', { name: 'Reset' }));
    expect(api.setStatus).toHaveBeenLastCalledWith('abc123', null, undefined);
  });

  it('saves notes on blur only when changed', async () => {
    const user = userEvent.setup();
    vi.mocked(api.patchJob).mockResolvedValue(jobDetail({ notes: 'Referral from Sam' }));
    renderJob();
    const notes = await screen.findByRole('textbox', { name: 'Notes' });
    await user.click(notes);
    await user.tab();
    expect(api.patchJob).not.toHaveBeenCalled();
    await user.type(notes, 'Referral from Sam');
    await user.tab();
    expect(api.patchJob).toHaveBeenCalledWith('abc123', { notes: 'Referral from Sam' });
  });
});

const saved = {
  draft_version: 1, draft_rules_version: 'r1234567',
  coverage: { covered: ['Python', 'SQL'], missing: ['PhD in statistics'], pct_required: 100, pct_preferred: 0 },
  unmet: ['PhD in statistics'],
  draft_history: [{ version: 1, at: '2026-10-15T10:00:00+00:00', rules_version: 'r1234567' }],
};

describe('JobPage Claude workbench', () => {
  it('Open in Claude copies the short prompt and links the packet Doc', async () => {
    const user = userEvent.setup();
    vi.mocked(api.openPacket).mockResolvedValue({
      prompt: "In this project, tailor my resume and cover letter for the job in the Google Doc 'Acme'.",
      doc_url: 'https://docs.google.com/document/d/P/edit', inline: false, rules_version: 'r1234567',
    });
    renderJob();
    await user.click(await screen.findByRole('button', { name: 'Open in Claude' }));
    expect(api.openPacket).toHaveBeenCalledWith('abc123');
    expect(api.copyText).toHaveBeenCalledWith(expect.stringContaining('In this project'));
    const status = await screen.findByRole('status');
    expect(status).toHaveTextContent('Copied the prompt');
    expect(status).toHaveTextContent('rules r1234567');
    expect(within(status).getByRole('link', { name: /Packet Doc/ })).toHaveAttribute('href', 'https://docs.google.com/document/d/P/edit');
    expect(within(status).getByRole('link', { name: /claude.ai/ })).toBeInTheDocument();
  });

  it('without Drive the full packet is copied', async () => {
    const user = userEvent.setup();
    vi.mocked(api.openPacket).mockResolvedValue({ prompt: 'FULL PACKET', doc_url: null, inline: true, rules_version: 'r1' });
    renderJob();
    await user.click(await screen.findByRole('button', { name: 'Open in Claude' }));
    expect(api.copyText).toHaveBeenCalledWith('FULL PACKET');
    expect(await screen.findByRole('status')).toHaveTextContent("Copied the full packet (Drive isn't set up)");
    expect(screen.queryByRole('link', { name: /Packet Doc/ })).toBeNull();
  });

  it('a failed copy shows the prompt to copy by hand', async () => {
    const user = userEvent.setup();
    vi.mocked(api.openPacket).mockResolvedValue({ prompt: 'FULL PACKET', doc_url: null, inline: true, rules_version: 'r1' });
    vi.mocked(api.copyText).mockRejectedValueOnce(new Error('denied'));
    renderJob();
    await user.click(await screen.findByRole('button', { name: 'Open in Claude' }));
    expect(await screen.findByRole('status')).toHaveTextContent("Couldn't copy: select the prompt below");
    expect(screen.getByRole('textbox', { name: 'Prompt' })).toHaveValue('FULL PACKET');
    expect(screen.queryByRole('link', { name: /Packet Doc/ })).toBeNull();
  });

  it('a failed paste lists the errors and copies the fix message', async () => {
    const user = userEvent.setup();
    vi.mocked(api.pasteResult).mockResolvedValue({
      ok: false, errors: ['Kubernetes is not backed by your library'], warnings: [],
      fix_prompt: 'These problems were found:\n1. Kubernetes is not backed by your library',
    });
    renderJob();
    const save = await screen.findByRole('button', { name: 'Check & save' });
    expect(save).toBeDisabled();
    fireEvent.change(screen.getByRole('textbox', { name: 'Paste result' }), { target: { value: '```json\n{}\n```' } });
    await user.click(save);
    expect(api.pasteResult).toHaveBeenCalledWith('abc123', '```json\n{}\n```');
    const alert = await screen.findByRole('alert');
    expect(within(alert).getByRole('list', { name: 'Problems' })).toHaveTextContent('Kubernetes is not backed');
    await user.click(within(alert).getByRole('button', { name: 'Copy fix message' }));
    expect(api.copyText).toHaveBeenCalledWith(expect.stringContaining('These problems were found'));
    expect(within(alert).getByRole('button', { name: 'Copied fix message' })).toBeInTheDocument();
  });

  it('a saved paste shows the files, coverage and draft history', async () => {
    const user = userEvent.setup();
    const links = {
      ...jobDetail().links, resume: 'https://drive.google.com/file/d/R/view',
      resume_docx: '/app/api/jobs/abc123/files/resume_docx', cover_letter: 'https://drive.google.com/file/d/C/view',
    };
    vi.mocked(api.pasteResult).mockResolvedValue({
      ok: true, outcome: { draft_version: 1, warnings: ["Dropped to fit one page: 'x'"], coverage: saved.coverage, unmet: saved.unmet },
      job: jobDetail({ ...saved, links }),
    });
    renderJob();
    fireEvent.change(await screen.findByRole('textbox', { name: 'Paste result' }), { target: { value: 'reply' } });
    await user.click(screen.getByRole('button', { name: 'Check & save' }));
    expect(await screen.findByText('Saved draft v1.')).toBeInTheDocument();
    expect(screen.getByRole('list', { name: 'Warnings' })).toHaveTextContent('Dropped to fit one page');
    const files = screen.getByRole('navigation', { name: 'Files' });
    expect(within(files).getByRole('link', { name: /Resume PDF/ })).toHaveAttribute('href', links.resume);
    expect(within(files).getByRole('link', { name: /Resume DOCX/ })).toHaveAttribute('href', links.resume_docx);
    expect(within(files).getByRole('link', { name: /Cover letter PDF/ })).toHaveAttribute('href', links.cover_letter);
    const targeting = screen.getByRole('region', { name: 'Resume targeting' });
    expect(targeting).toHaveTextContent('100% of the required qualifications');
    expect(within(targeting).getByRole('list', { name: 'Not on your resume' })).toHaveTextContent('PhD in statistics');
    expect(screen.getByRole('list', { name: 'Draft history' })).toHaveTextContent('v1 · 2026-10-15 · rules r1234567');
  });

  it('a job without a draft shows no coverage', async () => {
    renderJob();
    await screen.findByRole('button', { name: 'Open in Claude' });
    expect(screen.queryByRole('region', { name: 'Resume targeting' })).toBeNull();
    expect(screen.queryByRole('button', { name: /Generate/ })).toBeNull();
  });
});

describe('JobPage fit breakdown', () => {
  it('shows the score, each component, the red flags and the AI summary', async () => {
    renderJob();
    const fit = await screen.findByRole('region', { name: 'Fit' });
    expect(fit).toHaveTextContent('Fit score 80/100');
    expect(fit).toHaveTextContent('Strong match on Python and SQL.');
    const items = within(fit).getAllByRole('listitem');
    expect(items[0]).toHaveTextContent('Salary✓Importantup to $150,000/year meets your $100,000 minimum');
    expect(items[1]).toHaveTextContent('Work mode✗Nice to have');
    expect(items[2]).toHaveTextContent('Skills4/5Very important');
    expect(within(fit).getByRole('heading', { name: 'Red flags' })).toBeInTheDocument();
    expect(fit).toHaveTextContent('Mostly sales: quota-carrying');
    expect(screen.queryByText(/Stale assessment/)).toBeNull();
  });

  it('shows dealbreaker reasons and the stale note', async () => {
    vi.mocked(api.getJob).mockResolvedValue(jobDetail({
      assessment_stale: true, fit_flags: ['salary'], flag_reasons: ['salary below minimum ($80,000/year < $100,000)'],
    }));
    renderJob();
    expect(await screen.findByRole('list', { name: 'Flags' })).toHaveTextContent('⚠ salary below minimum');
    expect(screen.getByText(/Stale assessment/)).toBeInTheDocument();
  });

  it('Rescore polls the task, then reloads the breakdown', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    vi.mocked(api.rescore).mockResolvedValue(task('queued', { kind: 'rescore' }));
    vi.mocked(api.getTask).mockResolvedValueOnce(task('done', { kind: 'rescore' }));
    renderJob();
    await user.click(await screen.findByRole('button', { name: 'Rescore' }));
    expect(api.rescore).toHaveBeenCalledWith('abc123');
    expect(await screen.findByRole('status')).toHaveTextContent('Queued: assessment');
    expect(screen.getByRole('button', { name: 'Rescore' })).toBeDisabled();

    vi.mocked(api.getJob).mockResolvedValue(jobDetail({ fit_score: 95 }));
    await act(() => vi.advanceTimersByTimeAsync(3000));
    expect(screen.getByRole('status')).toHaveTextContent('Finished assessment');
    await waitFor(() => expect(screen.getByRole('region', { name: 'Fit' })).toHaveTextContent('Fit score 95/100'));
    expect(screen.getByRole('button', { name: 'Rescore' })).toBeEnabled();
  });

  it('a budget stop shows the API message', async () => {
    const user = userEvent.setup();
    vi.mocked(api.rescore).mockRejectedValue(new Error('Monthly LLM budget reached: $5.00 spent'));
    renderJob();
    await user.click(await screen.findByRole('button', { name: 'Rescore' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('Monthly LLM budget reached');
  });

  it('explains a job Claude has not assessed yet', async () => {
    vi.mocked(api.getJob).mockResolvedValue(jobDetail({ fit_assessment: null, fit_score: 60 }));
    renderJob();
    expect(await screen.findByText(/Not assessed by Claude yet/)).toBeInTheDocument();
  });
});
