import { useCallback, useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import * as api from '../api';
import BlurField from '../components/BlurField';
import { SEARCH_DEBOUNCE_MS } from '../components/ContactsPanel';
import { RELATION_LABELS } from '../labels';
import type { ContactFields, ContactListItem, ContactWithJobs } from '../types';

function ContactEditor({ id, onChanged, onDeleted }: { id: string; onChanged: () => void; onDeleted: () => void }) {
  const [contact, setContact] = useState<ContactWithJobs | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const ctrl = new AbortController();
    setContact(null);
    api.getContact(id, ctrl.signal).then(setContact).catch((err) => {
      if (!ctrl.signal.aborted) setError(err.message || String(err));
    });
    return () => ctrl.abort();
  }, [id]);

  async function save(fields: ContactFields) {
    setError(null);
    try {
      setContact(await api.updateContact(id, fields));
      onChanged();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }

  async function remove() {
    if (!contact || !window.confirm(`Delete ${contact.name}? They are unlinked from every job.`)) return;
    try {
      await api.deleteContact(id);
      onDeleted();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }

  if (!contact) return error ? <p className="error" role="alert">{error}</p> : <p className="muted">Loading…</p>;
  const field = (label: string, key: 'company' | 'role' | 'email' | 'phone' | 'linkedin_url', type = 'text') => (
    <BlurField label={label} type={type} value={contact[key]} onSave={(v) => save({ [key]: v })} />
  );
  return (
    <section className="card contact-editor" aria-label={`Edit ${contact.name}`}>
      <div className="form-grid">
        <BlurField label="Name" value={contact.name} onSave={(v) => { if (v) save({ name: v }); }} />
        {field('Company', 'company')}
        {field('Role', 'role')}
        {field('Email', 'email', 'email')}
        {field('Phone', 'phone', 'tel')}
        {field('LinkedIn', 'linkedin_url', 'url')}
      </div>
      <BlurField label="Notes" multiline value={contact.notes} onSave={(v) => save({ notes: v })} />
      <h3>Jobs</h3>
      {contact.jobs.length ? (
        <ul>
          {contact.jobs.map((j) => (
            <li key={j.key}>
              <Link to={`/jobs/${j.key}`}>{j.title || '(untitled)'}</Link>
              {j.company && <span className="muted"> · {j.company}</span>}
              <span className="muted"> · {RELATION_LABELS[j.relation] ?? j.relation}</span>
            </li>
          ))}
        </ul>
      ) : (
        <p className="muted">Not linked to any job.</p>
      )}
      <div className="dialog-actions">
        <button type="button" className="danger" onClick={remove}>Delete contact</button>
      </div>
      {error && <p className="error" role="alert">{error}</p>}
    </section>
  );
}

/** Every contact, searchable; a row opens its editor with the jobs it's linked to. */
export default function ContactsPage() {
  const [q, setQ] = useState('');
  const [contacts, setContacts] = useState<ContactListItem[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<string | null>(null);

  const load = useCallback((signal?: AbortSignal) => {
    api.listContacts(q.trim() || undefined, signal).then(setContacts).catch((err) => {
      if (!signal?.aborted) setError(err.message || String(err));
    });
  }, [q]);

  useEffect(() => {
    const ctrl = new AbortController();
    const timer = setTimeout(() => load(ctrl.signal), SEARCH_DEBOUNCE_MS);
    return () => {
      clearTimeout(timer);
      ctrl.abort();
    };
  }, [load]);

  return (
    <section className="contacts-page">
      <div className="list-header">
        <h1>Contacts</h1>
        {contacts && <span className="muted">{contacts.length.toLocaleString()} shown</span>}
      </div>
      <label className="field grow">
        <span>Search</span>
        <input type="search" placeholder="Name, company or email" value={q} onChange={(e) => setQ(e.target.value)} />
      </label>
      {error && <p className="notice error" role="alert">Couldn't load contacts: {error}</p>}
      <div className="contacts-layout">
        {contacts && contacts.length === 0 ? (
          <p className="notice">No contacts{q.trim() ? ' match this search' : ' yet: add them from a job page'}.</p>
        ) : contacts ? (
          <div className="table-scroll">
            <table className="jobs-table contacts-table">
              <thead>
                <tr><th>Name</th><th>Company</th><th>Role</th><th>Email</th><th className="num">Jobs</th></tr>
              </thead>
              <tbody>
                {contacts.map((c) => (
                  <tr key={c.id} className={`job-row${selected === c.id ? ' selected' : ''}`}
                    onClick={(e) => {
                      if ((e.target as HTMLElement).closest('a')) return;
                      setSelected(c.id);
                    }}>
                    <td className="col-title">
                      <button type="button" className="link-button" onClick={() => setSelected(c.id)}>{c.name}</button>
                    </td>
                    <td>{c.company || '—'}</td>
                    <td>{c.role || '—'}</td>
                    <td>{c.email ? <a href={`mailto:${c.email}`}>{c.email}</a> : '—'}</td>
                    <td className="num">{c.job_count}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : null}
        {selected && (
          <ContactEditor key={selected} id={selected} onChanged={() => load()}
            onDeleted={() => {
              setSelected(null);
              load();
            }} />
        )}
      </div>
    </section>
  );
}
