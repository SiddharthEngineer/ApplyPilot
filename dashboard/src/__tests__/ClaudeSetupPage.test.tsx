import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import * as api from '../api';
import ClaudeSetupPage from '../pages/ClaudeSetupPage';
import type { ClaudeSetup } from '../types';

vi.mock('../api');

function setup(extra: Partial<ClaudeSetup> = {}): ClaudeSetup {
  return {
    drive: true, drive_detail: '~/.strelitzia/google_token.json', rules_version: 'r1234567',
    docs: [
      { kind: 'rules', name: 'Strelitzia rules', url: 'https://docs.google.com/document/d/R/edit', changed_at: '2026-10-15T09:30:00+00:00' },
      { kind: 'library', name: 'Strelitzia content library', url: 'https://docs.google.com/document/d/L/edit', changed_at: '2026-10-14T08:00:00+00:00' },
    ],
    project_name: 'Strelitzia Resumes',
    project_instructions: 'You tailor my resume and cover letter for one job per chat.',
    ...extra,
  };
}

const renderPage = () => render(<MemoryRouter><ClaudeSetupPage /></MemoryRouter>);

beforeEach(() => {
  vi.mocked(api.getClaudeSetup).mockResolvedValue(setup());
});

describe('ClaudeSetupPage', () => {
  it('lists the steps, links both Docs, and shows when the rules changed', async () => {
    renderPage();
    expect(await screen.findByRole('heading', { name: 'Claude setup' })).toBeInTheDocument();
    expect(screen.getByText('Strelitzia Resumes')).toBeInTheDocument();
    const docs = screen.getByRole('list', { name: 'Project Docs' });
    expect(within(docs).getByRole('link', { name: /Strelitzia rules/ })).toHaveAttribute('href', 'https://docs.google.com/document/d/R/edit');
    expect(within(docs).getByRole('link', { name: /Strelitzia content library/ })).toBeInTheDocument();
    expect(docs).toHaveTextContent('last changed 2026-10-15 09:30');
    expect(screen.getByText(/Rules version r1234567/)).toBeInTheDocument();
  });

  it('copies the project instructions', async () => {
    const user = userEvent.setup();
    renderPage();
    await user.click(await screen.findByRole('button', { name: 'Copy instructions' }));
    expect(api.copyText).toHaveBeenCalledWith('You tailor my resume and cover letter for one job per chat.');
    expect(screen.getByRole('button', { name: 'Copied' })).toBeInTheDocument();
  });

  it('writes missing Docs to Drive', async () => {
    const user = userEvent.setup();
    vi.mocked(api.getClaudeSetup).mockResolvedValue(setup({
      docs: setup().docs.map((d) => ({ ...d, url: null, changed_at: null })),
    }));
    vi.mocked(api.publishClaudeDocs).mockResolvedValue(setup());
    renderPage();
    expect(await screen.findAllByText(/not written yet/)).toHaveLength(2);
    await user.click(screen.getByRole('button', { name: 'Write the Docs to Drive' }));
    expect(api.publishClaudeDocs).toHaveBeenCalled();
    expect(await screen.findByRole('link', { name: /Strelitzia rules/ })).toBeInTheDocument();
  });

  it('explains the clipboard fallback without Drive', async () => {
    vi.mocked(api.getClaudeSetup).mockResolvedValue(setup({ drive: false, drive_detail: 'Run `strelitzia drive auth`' }));
    renderPage();
    expect(await screen.findByText(/Google Drive isn't set up/)).toBeInTheDocument();
    expect(screen.queryByRole('list', { name: 'Project Docs' })).toBeNull();
    expect(screen.queryByRole('button', { name: /Docs/ })).toBeNull();
  });
});
