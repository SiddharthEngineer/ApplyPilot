import { useEffect, useState } from 'react';
import type { FormEvent } from 'react';
import * as api from '../api';
import Flower from '../components/Flower';
import type { AdminUser } from '../types';

const STATUS_LABELS: Record<AdminUser['status'], string> = {
  invited: 'Invited', active: 'Active', disabled: 'Disabled',
};

const usd = (n: number) => `$${n.toFixed(2)}`;

/** A cap input that saves on blur or Enter when the value changed. Empty = no cap. */
function CapField({ user, onSave }: { user: AdminUser; onSave: (cap: number | null) => void }) {
  const current = user.monthly_cap_usd === null ? '' : String(user.monthly_cap_usd);
  const [text, setText] = useState(current);
  useEffect(() => setText(current), [current]);
  const save = () => {
    if (text.trim() === current) return;
    const cap = text.trim() === '' ? null : Number(text);
    if (cap !== null && (!Number.isFinite(cap) || cap < 0)) {
      setText(current);
      return;
    }
    onSave(cap);
  };
  return (
    <input
      type="number" min="0" step="0.5" className="cap-input" value={text} placeholder="No cap"
      aria-label={`Monthly cap for ${user.email}`}
      onChange={(e) => setText(e.target.value)} onBlur={save}
      onKeyDown={(e) => { if (e.key === 'Enter') e.currentTarget.blur(); }}
    />
  );
}

/** The owner's Users page: invite emails, see this month's spend, change caps, disable and re-enable accounts. */
export default function UsersPage() {
  const [rows, setRows] = useState<AdminUser[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [email, setEmail] = useState('');
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    const ctrl = new AbortController();
    api.listUsers(ctrl.signal).then(setRows).catch((err) => {
      if (!ctrl.signal.aborted) setError(err.message || String(err));
    });
    return () => ctrl.abort();
  }, []);

  const replace = (u: AdminUser) => setRows((prev) => prev && prev.map((r) => (r.id === u.id ? u : r)));

  async function run(action: () => Promise<void>) {
    setBusy(true);
    setError(null);
    try {
      await action();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  function invite(e: FormEvent) {
    e.preventDefault();
    if (!email.trim()) return;
    run(async () => {
      const u = await api.inviteUser(email.trim());
      setRows((prev) => [...(prev ?? []), u]);
      setEmail('');
    });
  }

  if (!rows) {
    return error ? <p className="notice error" role="alert">Couldn't load the users: {error}</p> : (
      <div className="loader-state" aria-live="polite" aria-busy="true"><Flower variant="loader" size={64} />Loading…</div>
    );
  }

  return (
    <article className="users-page">
      <h1>Users</h1>
      <p className="muted">
        Invite a Google account by email; it can sign in once invited. Each user has their own jobs and Drive, and a
        monthly cap on Claude spend. Disabling an account keeps its data, so you can enable it again.
      </p>
      {error && <p className="error" role="alert">{error}</p>}
      <form className="invite-form" onSubmit={invite}>
        <label className="field">
          <span>Invite an email</span>
          <input type="email" required value={email} placeholder="name@gmail.com" onChange={(e) => setEmail(e.target.value)} />
        </label>
        <button type="submit" className="primary" disabled={busy || !email.trim()}>Invite</button>
      </form>
      <div className="table-scroll">
        <table className="jobs-table users-table">
          <thead>
            <tr>
              <th>User</th><th>Status</th><th className="num">Spend this month</th><th>Monthly cap (USD)</th>
              <th>Last sign-in</th><th aria-label="Actions" />
            </tr>
          </thead>
          <tbody>
            {rows.map((u) => (
              <tr key={u.id}>
                <td>
                  {u.name && <div>{u.name}</div>}
                  <div className={u.name ? 'muted small' : undefined}>{u.email}</div>
                  {u.role === 'owner' && <span className="muted small">Owner</span>}
                </td>
                <td>{STATUS_LABELS[u.status]}</td>
                <td className="num">{usd(u.month_spend_usd)}</td>
                <td>
                  {u.role === 'owner' ? <span className="muted" title="LLM_MONTHLY_BUDGET_USD limits the whole server">No cap</span> : (
                    <CapField user={u} onSave={(cap) => run(async () => replace(await api.updateUser(u.id, { monthly_cap_usd: cap })))} />
                  )}
                </td>
                <td>{u.last_login_at ? u.last_login_at.slice(0, 16).replace('T', ' ') : <span className="muted">Never</span>}</td>
                <td>
                  {u.role !== 'owner' && (u.status === 'disabled' ? (
                    <button type="button" disabled={busy} aria-label={`Enable ${u.email}`}
                      onClick={() => run(async () => replace(await api.updateUser(u.id, { status: 'active' })))}>Enable</button>
                  ) : (
                    <button type="button" className="danger" disabled={busy} aria-label={`Disable ${u.email}`}
                      onClick={() => run(async () => replace(await api.disableUser(u.id)))}>Disable</button>
                  ))}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </article>
  );
}
