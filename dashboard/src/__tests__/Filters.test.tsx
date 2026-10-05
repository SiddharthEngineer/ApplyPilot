import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, useLocation } from 'react-router-dom';
import { describe, expect, it } from 'vitest';
import Filters from '../components/Filters';
import { parseQuery, toSearchParams, useQueryState } from '../useQueryState';
import { FACETS } from './fixtures';

function Harness() {
  const [query, update] = useQueryState();
  const loc = useLocation();
  return (
    <>
      <Filters query={query} facets={FACETS} onChange={update} />
      <div data-testid="search">{loc.search}</div>
    </>
  );
}

function renderFilters(url = '/') {
  render(
    <MemoryRouter initialEntries={[url]}>
      <Harness />
    </MemoryRouter>,
  );
  return { user: userEvent.setup(), search: () => decodeURIComponent(screen.getByTestId('search').textContent ?? '') };
}

describe('Filters', () => {
  it('status segments write status and show facet counts', async () => {
    const { user, search } = renderFilters();
    const group = screen.getByRole('group', { name: 'Status' });
    expect(within(group).getByRole('button', { name: /All/ })).toHaveAttribute('aria-pressed', 'true');
    expect(within(group).getByRole('button', { name: /Submitted/ })).toHaveTextContent('2');
    await user.click(within(group).getByRole('button', { name: /No response/ }));
    expect(search()).toBe('?status=no_response');
    await user.click(within(group).getByRole('button', { name: /All/ }));
    expect(search()).toBe('');
  });

  it('role checkboxes write repeated role params with readable labels', async () => {
    const { user, search } = renderFilters();
    await user.click(screen.getByText('Role'));
    await user.click(screen.getByRole('checkbox', { name: /Data Science/ }));
    await user.click(screen.getByRole('checkbox', { name: /ML\/AI Engineering/ }));
    expect(search()).toBe('?role=data_science&role=ml_ai_engineering');
    await user.click(screen.getByRole('checkbox', { name: /Data Science/ }));
    expect(search()).toBe('?role=ml_ai_engineering');
  });

  it('work mode toggles write work_mode', async () => {
    const { user, search } = renderFilters();
    const group = screen.getByRole('group', { name: 'Work mode' });
    await user.click(within(group).getByRole('button', { name: 'Remote' }));
    await user.click(within(group).getByRole('button', { name: 'On-site' }));
    expect(search()).toBe('?work_mode=remote&work_mode=onsite');
  });

  it('debounces the search and location text', async () => {
    const { user, search } = renderFilters();
    await user.type(screen.getByPlaceholderText('Title or company'), 'snowflake');
    expect(search()).toBe(''); // not yet
    await waitFor(() => expect(search()).toBe('?q=snowflake'));
    await user.type(screen.getByPlaceholderText('City, state or Remote'), 'Remote');
    await waitFor(() => expect(search()).toBe('?q=snowflake&location=Remote'));
  });

  it('date and score ranges write their params', async () => {
    const { user, search } = renderFilters();
    await user.type(screen.getByLabelText('Found from'), '2026-10-01');
    await user.type(screen.getByLabelText('Due to'), '2026-12-31');
    await user.selectOptions(screen.getByLabelText('Fit score min'), '7');
    await user.selectOptions(screen.getByLabelText('Fit score max'), '9');
    expect(search()).toBe('?found_from=2026-10-01&due_to=2026-12-31&score_min=7&score_max=9');
  });

  it('restores every control from the URL, and Clear keeps only the sort', async () => {
    const url =
      '/?status=submitted&role=data_science&work_mode=remote&location=Chicago&found_from=2026-10-01&found_to=2026-10-04' +
      '&due_from=2026-10-10&due_to=2026-11-01&score_min=6&score_max=9&q=acme&sort=fit_score&page=3';
    const { user, search } = renderFilters(url);
    expect(within(screen.getByRole('group', { name: 'Status' })).getByRole('button', { name: /Submitted/ }))
      .toHaveAttribute('aria-pressed', 'true');
    expect(screen.getByText('Role (1)')).toBeInTheDocument();
    expect(screen.getByRole('checkbox', { name: /Data Science/ })).toBeChecked();
    expect(screen.getByRole('button', { name: 'Remote' })).toHaveAttribute('aria-pressed', 'true');
    expect(screen.getByPlaceholderText('City, state or Remote')).toHaveValue('Chicago');
    expect(screen.getByPlaceholderText('Title or company')).toHaveValue('acme');
    expect(screen.getByLabelText('Found from')).toHaveValue('2026-10-01');
    expect(screen.getByLabelText('Found to')).toHaveValue('2026-10-04');
    expect(screen.getByLabelText('Due from')).toHaveValue('2026-10-10');
    expect(screen.getByLabelText('Due to')).toHaveValue('2026-11-01');
    expect(screen.getByLabelText('Fit score min')).toHaveValue('6');
    expect(screen.getByLabelText('Fit score max')).toHaveValue('9');

    await user.click(screen.getByRole('button', { name: 'Clear filters' }));
    expect(search()).toBe('?sort=fit_score');
    expect(screen.getByPlaceholderText('Title or company')).toHaveValue('');
    expect(screen.getByRole('button', { name: 'Clear filters' })).toBeDisabled();
  });

  it('a filter change goes back to page 1', async () => {
    const { user, search } = renderFilters('/?page=4');
    await user.click(screen.getByRole('button', { name: 'Hybrid' }));
    expect(search()).toBe('?work_mode=hybrid');
  });
});

describe('query parsing', () => {
  it('drops invalid values and defaults', () => {
    const q = parseQuery(new URLSearchParams('status=bogus&score_min=11&score_max=x&found_from=yesterday&sort=-discovered_at&page=1'));
    expect(q).toEqual({});
    expect(toSearchParams({ sort: '-discovered_at', page: 1, q: '' }).toString()).toBe('');
  });
});
