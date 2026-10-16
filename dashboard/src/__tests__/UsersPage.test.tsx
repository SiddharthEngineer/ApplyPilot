import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import * as api from '../api';
import App from '../App';
import UsersPage from '../pages/UsersPage';
import type { AdminUser, AuthMe } from '../types';

vi.mock('../api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../api')>()),
  getMe: vi.fn(),
  getPanel: vi.fn(),
  onUnauthorized: vi.fn(),
  listUsers: vi.fn(),
  inviteUser: vi.fn(),
  updateUser: vi.fn(),
  disableUser: vi.fn(),
}));

function user(extra: Partial<AdminUser> = {}): AdminUser {
  return {
    id: 'u2', email: 'friend@example.com', name: 'Friend', role: 'user', status: 'active', monthly_cap_usd: 5,
    month_spend_usd: 1.234, last_login_at: '2026-10-15T09:30:00+00:00', ...extra,
  };
}

const OWNER_ROW = user({ id: 'u1', email: 'owner@example.com', name: 'Ada', role: 'owner', monthly_cap_usd: null, month_spend_usd: 0 });

const renderPage = () => render(<MemoryRouter><UsersPage /></MemoryRouter>);
const rowFor = (email: string) => screen.getByText(email).closest('tr')!;

beforeEach(() => {
  vi.mocked(api.listUsers).mockResolvedValue([OWNER_ROW, user()]);
});

afterEach(() => vi.clearAllMocks());

describe('UsersPage', () => {
  it('lists users with spend, cap and last sign-in', async () => {
    renderPage();
    const row = within((await screen.findByText('friend@example.com')).closest('tr')!);
    expect(row.getByText('Active')).toBeInTheDocument();
    expect(row.getByText('$1.23')).toBeInTheDocument();
    expect(row.getByLabelText('Monthly cap for friend@example.com')).toHaveValue(5);
    expect(row.getByText('2026-10-15 09:30')).toBeInTheDocument();
    // The owner can't be disabled.
    expect(within(rowFor('owner@example.com')).queryByRole('button')).toBeNull();
    expect(within(rowFor('owner@example.com')).getByText('No cap')).toBeInTheDocument();
    expect(within(rowFor('owner@example.com')).queryByLabelText(/Monthly cap/)).toBeNull();
  });

  it('invites an email', async () => {
    const u = userEvent.setup();
    vi.mocked(api.inviteUser).mockResolvedValue(user({ id: 'u3', email: 'new@example.com', name: null, status: 'invited', last_login_at: null }));
    renderPage();
    await u.type(await screen.findByLabelText('Invite an email'), 'new@example.com');
    await u.click(screen.getByRole('button', { name: 'Invite' }));
    expect(api.inviteUser).toHaveBeenCalledWith('new@example.com');
    const row = within(await screen.findByText('new@example.com').then((el) => el.closest('tr')!));
    expect(row.getByText('Invited')).toBeInTheDocument();
    expect(row.getByText('Never')).toBeInTheDocument();
    expect(screen.getByLabelText('Invite an email')).toHaveValue('');
  });

  it('shows why an invite failed', async () => {
    const u = userEvent.setup();
    vi.mocked(api.inviteUser).mockRejectedValue(new api.ApiError(409, 'friend@example.com already has an account'));
    renderPage();
    await u.type(await screen.findByLabelText('Invite an email'), 'friend@example.com');
    await u.click(screen.getByRole('button', { name: 'Invite' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('already has an account');
  });

  it('edits the cap inline, and an empty cap means no cap', async () => {
    const u = userEvent.setup();
    vi.mocked(api.updateUser).mockResolvedValueOnce(user({ monthly_cap_usd: 12 }))
      .mockResolvedValueOnce(user({ monthly_cap_usd: null }));
    renderPage();
    const cap = await screen.findByLabelText('Monthly cap for friend@example.com');
    await u.clear(cap);
    await u.type(cap, '12{Enter}');
    expect(api.updateUser).toHaveBeenCalledWith('u2', { monthly_cap_usd: 12 });
    await u.clear(cap);
    await u.tab();
    expect(api.updateUser).toHaveBeenLastCalledWith('u2', { monthly_cap_usd: null });
    expect(cap).toHaveValue(null);
  });

  it('does not save an unchanged cap', async () => {
    const u = userEvent.setup();
    renderPage();
    await u.click(await screen.findByLabelText('Monthly cap for friend@example.com'));
    await u.tab();
    expect(api.updateUser).not.toHaveBeenCalled();
  });

  it('disables and enables a user', async () => {
    const u = userEvent.setup();
    vi.mocked(api.disableUser).mockResolvedValue(user({ status: 'disabled' }));
    vi.mocked(api.updateUser).mockResolvedValue(user({ status: 'active' }));
    renderPage();
    await u.click(await screen.findByRole('button', { name: 'Disable friend@example.com' }));
    expect(api.disableUser).toHaveBeenCalledWith('u2');
    expect(await within(rowFor('friend@example.com')).findByText('Disabled')).toBeInTheDocument();
    await u.click(screen.getByRole('button', { name: 'Enable friend@example.com' }));
    expect(api.updateUser).toHaveBeenCalledWith('u2', { status: 'active' });
    expect(await within(rowFor('friend@example.com')).findByText('Active')).toBeInTheDocument();
  });

  it('shows a load error', async () => {
    vi.mocked(api.listUsers).mockRejectedValue(new api.ApiError(403, 'Only the owner can manage users'));
    renderPage();
    expect(await screen.findByRole('alert')).toHaveTextContent('Only the owner can manage users');
  });
});

describe('Users nav link', () => {
  const renderApp = (me: AuthMe) => {
    vi.mocked(api.getMe).mockResolvedValue(me);
    vi.mocked(api.getPanel).mockResolvedValue({ due: null } as Awaited<ReturnType<typeof api.getPanel>>);
    return render(<MemoryRouter initialEntries={['/users']}><App /></MemoryRouter>);
  };

  it('is there for the owner and opens the Users page', async () => {
    renderApp({ enabled: true, user: { email: 'owner@example.com', name: 'Ada', role: 'owner' } });
    const nav = screen.getByRole('navigation', { name: 'Main' });
    expect(await within(nav).findByRole('link', { name: 'Users' })).toBeInTheDocument();
    expect(await screen.findByRole('heading', { name: 'Users' })).toBeInTheDocument();
  });

  it('waits for the account instead of showing Page not found', async () => {
    vi.mocked(api.getMe).mockReturnValue(new Promise(() => {}));
    vi.mocked(api.getPanel).mockResolvedValue({ due: null } as Awaited<ReturnType<typeof api.getPanel>>);
    render(<MemoryRouter initialEntries={['/users']}><App /></MemoryRouter>);
    expect(screen.getByText('Loading…')).toBeInTheDocument();
    expect(screen.queryByText(/Page not found/)).toBeNull();
  });

  it('is hidden for other users, and the route is not found', async () => {
    renderApp({ enabled: true, user: { email: 'friend@example.com', name: 'Friend', role: 'user' } });
    expect(await screen.findByText('Friend')).toBeInTheDocument();
    expect(within(screen.getByRole('navigation', { name: 'Main' })).queryByRole('link', { name: 'Users' })).toBeNull();
    expect(screen.getByText(/Page not found/)).toBeInTheDocument();
    expect(api.listUsers).not.toHaveBeenCalled();
  });
});
