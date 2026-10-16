import { act, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import * as api from '../api';
import JobsTable from '../components/JobsTable';
import JobsPage from '../pages/JobsPage';
import type { Status, Task } from '../types';
import { COLORS, FACETS, jobList, listItem } from './fixtures';

vi.mock('../api');

function LocationProbe() {
  const loc = useLocation();
  return <div data-testid="location">{loc.pathname + loc.search}</div>;
}

function renderPage(url = '/') {
  return render(
    <MemoryRouter initialEntries={[url]}>
      <Routes>
        <Route path="/" element={<JobsPage />} />
        <Route path="/jobs/:key" element={<p>detail page</p>} />
      </Routes>
      <LocationProbe />
    </MemoryRouter>,
  );
}

describe('JobsTable', () => {
  it('renders a badge in the API color for each status', () => {
    const statuses = Object.keys(COLORS) as Status[];
    render(
      <MemoryRouter>
        <JobsTable items={statuses.map((s, i) => listItem(s, i))} sort="-discovered_at" onSort={() => {}} />
      </MemoryRouter>,
    );
    const rows = screen.getAllByRole('row').slice(1);
    statuses.forEach((status, i) => {
      const badge = rows[i].querySelector('.badge')!;
      expect(badge).toHaveClass(`badge-${COLORS[status]}`);
    });
    expect(within(rows[3]).getByText('Submitted')).toBeInTheDocument();
    expect(within(rows[3]).getByText('3')).toBeInTheDocument(); // days since submitted
  });

  it('shows fit as 0-100 with a bar, and a warning with the reasons on flagged rows', () => {
    render(
      <MemoryRouter>
        <JobsTable sort="-discovered_at" onSort={() => {}} items={[
          listItem('active', 1, { fit_score: 82 }),
          listItem('active', 2, { fit_score: 40, fit_flags: ['salary', 'prefilter'],
            flag_reasons: ['salary below minimum', "title doesn't match your target roles"] }),
          listItem('active', 3, { fit_score: null }),
        ]} />
      </MemoryRouter>,
    );
    const rows = screen.getAllByRole('row').slice(1);
    expect(rows[0].querySelector('.fit-cell')).toHaveTextContent('82');
    expect(rows[0].querySelector('.fit-bar-fill')).toHaveClass('fit-high');
    expect(rows[0].querySelector('.flag')).toBeNull();
    const flag = within(rows[1]).getByLabelText(/Flagged/);
    expect(flag).toHaveAttribute('title', "salary below minimum\ntitle doesn't match your target roles");
    expect(rows[1].querySelector('.fit-bar-fill')).toHaveClass('fit-low');
    expect(rows[2].querySelector('.fit-cell')).toHaveTextContent('—');
  });

  it('links each row to its detail page', () => {
    render(
      <MemoryRouter>
        <JobsTable items={[listItem('active', 7)]} sort="-discovered_at" onSort={() => {}} />
      </MemoryRouter>,
    );
    expect(screen.getByRole('link', { name: 'Job 7' })).toHaveAttribute('href', '/jobs/key7');
  });

  it('marks the active sort column', () => {
    render(
      <MemoryRouter>
        <JobsTable items={[]} sort="fit_score" onSort={() => {}} />
      </MemoryRouter>,
    );
    expect(screen.getByRole('columnheader', { name: /Fit/ })).toHaveAttribute('aria-sort', 'ascending');
    expect(screen.getByRole('columnheader', { name: /Found/ })).toHaveAttribute('aria-sort', 'none');
  });
});

describe('JobsPage', () => {
  beforeEach(() => {
    vi.mocked(api.listJobs).mockResolvedValue(jobList([listItem('active', 1), listItem('submitted', 2)], 120));
    vi.mocked(api.getFacets).mockResolvedValue(FACETS);
  });

  it('shows the total and loads page 1 sorted by found date', async () => {
    renderPage();
    expect(screen.getByText('Loading jobs…').querySelector('.flower-loader')).not.toBeNull();
    expect(await screen.findByTestId('total')).toHaveTextContent('1–50 of 120');
    expect(api.listJobs).toHaveBeenLastCalledWith({ page_size: 50 }, expect.any(AbortSignal));
  });

  it('writes the sort to the URL and refetches when a header is clicked', async () => {
    const user = userEvent.setup();
    renderPage();
    await screen.findByText('Job 1');
    await user.click(screen.getByRole('button', { name: /Fit/ }));
    expect(screen.getByTestId('location')).toHaveTextContent('/?sort=-fit_score');
    await waitFor(() =>
      expect(api.listJobs).toHaveBeenLastCalledWith({ sort: '-fit_score', page_size: 50 }, expect.any(AbortSignal)),
    );
    await user.click(screen.getByRole('button', { name: /Fit/ }));
    expect(screen.getByTestId('location')).toHaveTextContent('/?sort=fit_score');
  });

  it('pages through results', async () => {
    const user = userEvent.setup();
    renderPage('/?sort=deadline');
    await screen.findByText('Job 1');
    await user.click(screen.getByRole('button', { name: 'Next →' }));
    expect(screen.getByTestId('location')).toHaveTextContent('/?sort=deadline&page=2');
  });

  it('navigates to the detail page when a row is clicked', async () => {
    const user = userEvent.setup();
    renderPage();
    await user.click(await screen.findByText('Company 2'));
    expect(screen.getByText('detail page')).toBeInTheDocument();
    expect(screen.getByTestId('location')).toHaveTextContent('/jobs/key2');
  });

  it('shows empty and error states', async () => {
    vi.mocked(api.listJobs).mockResolvedValueOnce(jobList([]));
    const { unmount } = renderPage();
    expect(await screen.findByText('No jobs match these filters.')).toBeInTheDocument();
    expect(document.querySelector('.flower-seedling')).not.toBeNull();
    unmount();
    vi.mocked(api.listJobs).mockRejectedValueOnce(new Error('boom'));
    renderPage();
    expect(await screen.findByRole('alert')).toHaveTextContent("Couldn't load jobs: boom");
  });

  describe('Rescore all', () => {
    const task = (state: Task['state'], error: string | null = null): Task => ({
      id: 't9', url: null, kind: 'rescore_all', state, created_at: '2026-10-16T10:00:00+00:00',
      started_at: state === 'queued' ? null : '2026-10-16T10:00:01+00:00',
      finished_at: state === 'done' || state === 'error' ? '2026-10-16T10:00:30+00:00' : null, error, key: null,
    });

    afterEach(() => {
      vi.useRealTimers();
    });

    it('shows the count, cost and skipped jobs, then queues and reloads the list when done', async () => {
      vi.useFakeTimers({ shouldAdvanceTime: true });
      const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
      vi.mocked(api.rescorePreview).mockResolvedValue({ jobs: 42, skipped_unextracted: 3, estimated_usd: 0.4 });
      vi.mocked(api.rescoreAll).mockResolvedValue({ jobs: 42, skipped_unextracted: 3, estimated_usd: 0.4,
        task: task('queued') });
      vi.mocked(api.getTask).mockResolvedValueOnce(task('done'));
      renderPage();
      await screen.findByText('Company 1');
      await user.click(screen.getByRole('button', { name: 'Rescore all' }));
      const dialog = await screen.findByRole('dialog');
      expect(dialog).toHaveTextContent('Re-assess 42 active jobs with Claude, about $0.40');
      expect(dialog).toHaveTextContent('3 jobs skipped: not extracted yet');
      expect(api.rescoreAll).not.toHaveBeenCalled();

      await user.click(within(dialog).getByRole('button', { name: 'Re-assess' }));
      expect(api.rescoreAll).toHaveBeenCalledTimes(1);
      expect(screen.queryByRole('dialog')).toBeNull();
      expect(await screen.findByRole('status')).toHaveTextContent('Queued: re-assessment of your active jobs');
      const loads = vi.mocked(api.listJobs).mock.calls.length;
      await act(() => vi.advanceTimersByTimeAsync(3000));
      expect(screen.getByRole('status')).toHaveTextContent('Finished re-assessment of your active jobs');
      await waitFor(() => expect(vi.mocked(api.listJobs).mock.calls.length).toBe(loads + 1));
    });

    it('cancel queues nothing, and a failed task or budget stop shows as an error', async () => {
      vi.useFakeTimers({ shouldAdvanceTime: true });
      const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
      vi.mocked(api.rescorePreview).mockResolvedValue({ jobs: 2, skipped_unextracted: 0, estimated_usd: 0.001 });
      renderPage();
      await screen.findByText('Company 1');
      await user.click(screen.getByRole('button', { name: 'Rescore all' }));
      expect(await screen.findByRole('dialog')).not.toHaveTextContent('skipped');
      await user.click(screen.getByRole('button', { name: 'Cancel' }));
      expect(screen.queryByRole('dialog')).toBeNull();
      expect(api.rescoreAll).not.toHaveBeenCalled();

      vi.mocked(api.rescoreAll).mockRejectedValueOnce(new Error('Monthly LLM budget reached'));
      await user.click(screen.getByRole('button', { name: 'Rescore all' }));
      await user.click(await screen.findByRole('button', { name: 'Re-assess' }));
      expect(await within(screen.getByRole('dialog')).findByRole('alert')).toHaveTextContent('budget reached');

      vi.mocked(api.rescoreAll).mockResolvedValueOnce({ jobs: 2, skipped_unextracted: 0, estimated_usd: 0.001,
        task: task('queued') });
      vi.mocked(api.getTask).mockResolvedValueOnce(task('error', 'The Claude API key is missing or was rejected'));
      await user.click(screen.getByRole('button', { name: 'Re-assess' }));
      await act(() => vi.advanceTimersByTimeAsync(3000));
      expect(screen.getByRole('status')).toHaveTextContent("Couldn't finish the re-assessment of your active jobs: "
        + 'The Claude API key is missing');
      expect(screen.getByRole('status')).toHaveAttribute('data-state', 'error');
    });
  });
});
