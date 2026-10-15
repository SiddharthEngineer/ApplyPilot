import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import * as api from '../api';
import PanelPage from '../pages/PanelPage';
import type { PanelOverview, PanelReport, PanelRun } from '../types';

vi.mock('../api');

function overview(extra: Partial<PanelOverview> = {}): PanelOverview {
  return {
    due: null, needed: 5, comparable: { latest_vs_previous: 5, rules_a_vs_b: 2 }, runs: [],
    project_name: 'Strelitzia Panel', project_instructions: 'You are a strict resume screener.',
    rubric: { name: 'Strelitzia panel rubric', url: null, text: '# Strelitzia panel rubric' },
    ...extra,
  };
}

const REPORT: PanelReport = {
  items_total: 2,
  items: [
    { item: 1, title: 'Data Engineer', company: 'Initech', older_version: 1, newer_version: 2, newer_shown: 'second',
      winners: { sonnet: 'newer', opus: 'older', you: 'newer' } },
    { item: 2, title: 'ML Engineer', company: 'Globex', older_version: 2, newer_version: 3, newer_shown: 'first',
      winners: { sonnet: 'newer', opus: 'newer', you: 'newer' } },
  ],
  graders: {
    you: { graded: 2, newer_win_rate: 1 },
    sonnet: { graded: 2, newer_win_rate: 1,
      a: { mean_overall: 2, mean_clarity: 3, met_rate: { required: 0.5, preferred: 0 }, red_flags: 2 },
      b: { mean_overall: 3.5, mean_clarity: 4, met_rate: { required: 1, preferred: 0.5 }, red_flags: 0 } },
    opus: { graded: 2, newer_win_rate: 0.5 },
  },
  agreement: { sonnet_vs_opus: 0.5, sonnet_vs_you: 1, opus_vs_you: 0.5 },
  verdict: 'newer is better',
  heard_back: [{ rules_version: 'r1', submitted: 3, heard_back: 1, rate: null, note: 'too few to compare' }],
};

function run(extra: Partial<PanelRun> = {}): PanelRun {
  return {
    id: 'run1', created_at: '2026-10-15T09:00:00+00:00', mode: 'latest_vs_previous', status: 'open',
    packets: [
      { packet: 1, model: 'sonnet', text: 'P1', prompt: 'Grade this blind resume panel… P1' },
      { packet: 2, model: 'opus', text: 'P2', prompt: 'Grade this blind resume panel… P2' },
    ],
    items: [
      { item: 1, title: 'Data Engineer', company: 'Initech', first_text: 'Resume one A', second_text: 'Resume one B', your_pick: null },
      { item: 2, title: 'ML Engineer', company: 'Globex', first_text: 'Resume two A', second_text: 'Resume two B', your_pick: null },
    ],
    graded: { sonnet: 0, opus: 0, you: 0 },
    report: null,
    ...extra,
  };
}

const renderPage = (path = '/panel') => render(<MemoryRouter initialEntries={[path]}><PanelPage /></MemoryRouter>);

beforeEach(() => {
  vi.mocked(api.getPanel).mockResolvedValue(overview());
  vi.mocked(api.getPanelRun).mockResolvedValue(run());
});

describe('PanelPage', () => {
  it('says how many more drafts are needed', async () => {
    vi.mocked(api.getPanel).mockResolvedValue(overview({ comparable: { latest_vs_previous: 3, rules_a_vs_b: 0 } }));
    renderPage();
    expect(await screen.findByTestId('comparable')).toHaveTextContent(
      '3 jobs have two comparable drafts. A panel needs 5: 2 more jobs need a second draft.');
    expect(screen.getByRole('button', { name: 'Create run' })).toBeEnabled();
  });

  it('shows the due reason and creates a run', async () => {
    const user = userEvent.setup();
    vi.mocked(api.getPanel).mockResolvedValue(overview({ due: 'no panel has been run yet' }));
    vi.mocked(api.createPanelRun).mockResolvedValue(run());
    renderPage();
    expect(await screen.findByText('Panel due: no panel has been run yet.')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Create run' }));
    expect(api.createPanelRun).toHaveBeenCalledWith(5, 'latest_vs_previous');
    expect(await screen.findByRole('region', { name: 'Model 1: Sonnet' })).toBeInTheDocument();
    expect(screen.getByRole('region', { name: 'Model 2: Opus' })).toHaveTextContent('opposite order');
  });

  it('copies packets and imports a model reply', async () => {
    const user = userEvent.setup();
    vi.mocked(api.getPanel).mockResolvedValue(overview({ runs: [{ id: 'run1', created_at: '2026-10-15T09:00:00+00:00',
      mode: 'latest_vs_previous', rules_version_a: 'r1', rules_version_b: 'r2', status: 'open' }] }));
    vi.mocked(api.importPanelGrades).mockResolvedValue({ ok: true, graded: 2, run: run({ graded: { sonnet: 2, opus: 0, you: 0 } }) });
    renderPage();
    const sonnet = await screen.findByRole('region', { name: 'Model 1: Sonnet' });
    expect(within(sonnet).getByText(/Use Sonnet, in a new chat in the Panel project/)).toBeInTheDocument();
    await user.click(within(sonnet).getByRole('button', { name: 'Copy packet for model 1' }));
    expect(api.copyText).toHaveBeenCalledWith('Grade this blind resume panel… P1');
    await user.type(within(sonnet).getByRole('textbox'), 'reply');
    await user.click(within(sonnet).getByRole('button', { name: 'Import grades' }));
    expect(api.importPanelGrades).toHaveBeenCalledWith('run1', 'sonnet', 'reply');
    expect(await within(sonnet).findByText('2 items graded.')).toBeInTheDocument();
    expect(within(sonnet).getByText('2 of 2 graded')).toBeInTheDocument();
  });

  it('shows import errors such as a count mismatch', async () => {
    const user = userEvent.setup();
    vi.mocked(api.importPanelGrades).mockResolvedValue({ ok: false, errors: ['The reply grades 1 items, but this panel has 2.'] });
    renderPage('/panel?run=run1');
    const opus = await screen.findByRole('region', { name: 'Model 2: Opus' });
    await user.type(within(opus).getByRole('textbox'), 'x');
    await user.click(within(opus).getByRole('button', { name: 'Import grades' }));
    expect(await within(opus).findByRole('alert')).toHaveTextContent('grades 1 items, but this panel has 2');
  });

  it('takes your blind picks and shows the report after the last one', async () => {
    const user = userEvent.setup();
    const afterFirst = run({ items: run().items.map((i) => (i.item === 1 ? { ...i, your_pick: 'second' } : i)) });
    vi.mocked(api.pickPanelItem).mockResolvedValueOnce(afterFirst)
      .mockResolvedValueOnce(run({ status: 'complete', items: afterFirst.items.map((i) => ({ ...i, your_pick: i.your_pick ?? 'first' })),
        report: REPORT }));
    renderPage('/panel?run=run1');
    const item1 = await screen.findByRole('group', { name: 'Item 1' });
    expect(within(item1).getByText('Resume one A')).toBeInTheDocument();
    expect(within(item1).getByText('Resume one B')).toBeInTheDocument();
    expect(screen.queryByText(/newer/)).toBeNull(); // blind
    await user.click(within(item1).getByRole('button', { name: 'Second' }));
    expect(api.pickPanelItem).toHaveBeenCalledWith('run1', 1, 'second');
    expect(await within(item1).findByRole('button', { name: 'Second', pressed: true })).toBeInTheDocument();
    expect(screen.queryByTestId('verdict')).toBeNull();
    await user.click(within(screen.getByRole('group', { name: 'Item 2' })).getByRole('button', { name: 'First' }));
    expect(await screen.findByTestId('verdict')).toHaveTextContent('Verdict: newer is better');
    expect(screen.getByRole('list', { name: 'Agreement' })).toHaveTextContent('Sonnet vs Opus: 50%');
    expect(screen.getByRole('list', { name: 'Items' })).toHaveTextContent('Opus older');
    expect(screen.getByRole('list', { name: 'Heard back' })).toHaveTextContent('1 of 3 heard back (too few to compare)');
    expect(screen.getByRole('row', { name: /Sonnet/ })).toHaveTextContent('2.0 → 3.5');
    // The report un-blinds the panel, so the picks are locked.
    expect(within(screen.getByRole('group', { name: 'Item 1' })).getByRole('button', { name: 'First' })).toBeDisabled();
    expect(screen.getByText(/Your picks are locked/)).toBeInTheDocument();
  });
});
