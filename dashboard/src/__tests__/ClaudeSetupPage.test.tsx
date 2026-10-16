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
    drive: true, drive_detail: 'Connected 2026-10-16', drive_source: 'user', connect_url: '/app/api/drive/connect',
    rules_version: 'r1234567',
    docs: [
      { kind: 'rules', name: 'Strelitzia rules', url: 'https://docs.google.com/document/d/R/edit', changed_at: '2026-10-15T09:30:00+00:00' },
      { kind: 'library', name: 'Strelitzia content library', url: 'https://docs.google.com/document/d/L/edit', changed_at: '2026-10-14T08:00:00+00:00' },
    ],
    project_name: 'Strelitzia Resumes',
    project_instructions: 'You tailor my resume and cover letter for one job per chat.',
    ...extra,
  };
}

const renderPage = (path = '/claude-setup') => render(
  <MemoryRouter initialEntries={[path]}><ClaudeSetupPage /></MemoryRouter>,
);

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

  it('offers Connect Google Drive when Drive is not connected', async () => {
    vi.mocked(api.getClaudeSetup).mockResolvedValue(setup({
      drive: false, drive_source: null, drive_detail: 'Connect Google Drive on the Claude setup page',
    }));
    renderPage();
    expect(await screen.findByRole('link', { name: 'Connect Google Drive' })).toHaveAttribute('href', '/app/api/drive/connect');
    expect(screen.queryByRole('button', { name: 'Disconnect' })).not.toBeInTheDocument();
  });

  it('shows the connect outcome and disconnects', async () => {
    const user = userEvent.setup();
    vi.mocked(api.disconnectDrive).mockResolvedValue({ ok: true });
    renderPage('/claude-setup?drive=connected');
    expect(await screen.findByText('Google Drive is connected.')).toBeInTheDocument();
    expect(screen.queryByRole('link', { name: 'Connect Google Drive' })).not.toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Reconnect' })).toHaveAttribute('href', '/app/api/drive/connect');
    vi.mocked(api.getClaudeSetup).mockResolvedValue(setup({ drive: false, drive_source: null }));
    await user.click(screen.getByRole('button', { name: 'Disconnect' }));
    expect(api.disconnectDrive).toHaveBeenCalled();
    expect(await screen.findByRole('link', { name: 'Connect Google Drive' })).toBeInTheDocument();
    expect(screen.queryByText('Google Drive is connected.')).not.toBeInTheDocument();
  });

  it('says why a connect failed', async () => {
    renderPage('/claude-setup?drive=denied');
    expect(await screen.findByText(/wasn't connected: allow access/)).toBeInTheDocument();
  });
});
