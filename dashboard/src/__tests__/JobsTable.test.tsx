import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import * as api from '../api';
import JobsTable from '../components/JobsTable';
import JobsPage from '../pages/JobsPage';
import type { Status } from '../types';
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
    await user.click(screen.getByRole('button', { name: /Next/ }));
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
});
