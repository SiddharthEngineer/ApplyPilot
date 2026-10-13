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

const generateButtons = () => ['Generate resume', 'Generate cover letter', 'Generate both']
  .map((name) => screen.getByRole('button', { name }));

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

describe('JobPage generation', () => {
  it('disables the generate buttons while a task runs', async () => {
    vi.mocked(api.listJobTasks).mockResolvedValue([task('running')]);
    vi.mocked(api.getTask).mockResolvedValue(task('running'));
    renderJob();
    await screen.findByRole('heading', { name: 'Data Scientist' });
    generateButtons().forEach((b) => expect(b).toBeDisabled());
    expect(screen.getByRole('status')).toHaveTextContent('Generating resume and cover letter');
  });

  it('Generate both polls until done, then shows the Drive links', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    const drive = {
      posting: 'https://example.com/job/1', apply: null, drive_folder: 'https://drive.google.com/drive/folders/F',
      resume: 'https://drive.google.com/file/d/R/view', cover_letter: 'https://drive.google.com/file/d/C/view',
    };
    vi.mocked(api.generate).mockResolvedValue(task('queued'));
    vi.mocked(api.getTask).mockResolvedValueOnce(task('running')).mockResolvedValueOnce(task('done'));
    renderJob();
    await user.click(await screen.findByRole('button', { name: 'Generate both' }));
    expect(api.generate).toHaveBeenCalledWith('abc123', { resume: true, cover: true });
    expect(await screen.findByRole('status')).toHaveTextContent('Queued');
    generateButtons().forEach((b) => expect(b).toBeDisabled());

    vi.mocked(api.getJob).mockResolvedValue(jobDetail({ status: 'in_progress', status_color: 'yellow', links: drive }));
    await act(() => vi.advanceTimersByTimeAsync(3000));
    expect(screen.getByRole('status')).toHaveTextContent('Generating');
    await act(() => vi.advanceTimersByTimeAsync(3000));
    expect(screen.getByRole('status')).toHaveTextContent('Generated resume and cover letter in 29s');
    expect(await screen.findByRole('link', { name: /Drive folder/ })).toHaveAttribute('href', drive.drive_folder);
    expect(screen.getByRole('link', { name: /Resume/ })).toHaveAttribute('href', drive.resume);
    expect(screen.getByRole('link', { name: /Cover letter/ })).toHaveAttribute('href', drive.cover_letter);
    generateButtons().forEach((b) => expect(b).toBeEnabled());
  });

  it('shows a failed task and the API error for a rejected request', async () => {
    const user = userEvent.setup();
    vi.mocked(api.listJobTasks).mockResolvedValue([task('error')]);
    vi.mocked(api.generate).mockRejectedValue(new Error('Choose a resume, a cover letter, or both')) // ApiError is auto-mocked;
    renderJob();
    expect(await screen.findByRole('status')).toHaveTextContent('Claude API is overloaded');
    await user.click(screen.getByRole('button', { name: 'Generate resume' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('Choose a resume');
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
    generateButtons().forEach((b) => expect(b).toBeEnabled()); // generation isn't blocked by a rescore

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
