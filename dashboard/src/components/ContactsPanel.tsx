import { useEffect, useRef, useState } from 'react';
import * as api from '../api';
import { RELATION_LABELS } from '../labels';
import type { ContactListItem, JobContact, JobDetail, Relation } from '../types';
import BlurField from './BlurField';

export const SEARCH_DEBOUNCE_MS = 250;
const RELATIONS = Object.keys(RELATION_LABELS) as Relation[];

/** Search-as-you-type over the shared contacts, with "Create '<text>'" at the bottom. */
function ContactPicker({ job, onLinked, onCancel }: {
  job: JobDetail;
  onLinked: (job: JobDetail, createdId?: string) => void;
  onCancel: () => void;
}) {
  const [text, setText] = useState('');
  const [relation, setRelation] = useState<Relation>('recruiter');
  const [results, setResults] = useState<ContactListItem[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const linked = new Set(job.contacts.map((c) => c.id));

  useEffect(() => inputRef.current?.focus(), []);
  useEffect(() => {
    const ctrl = new AbortController();
    const timer = setTimeout(() => {
      api.listContacts(text.trim() || undefined, ctrl.signal).then(setResults).catch(() => {});
    }, SEARCH_DEBOUNCE_MS);
    return () => {
      clearTimeout(timer);
      ctrl.abort();
    };
  }, [text]);

  async function link(fn: () => Promise<JobDetail>, created = false) {
    setBusy(true);
    setError(null);
    try {
      const updated = await fn();
      const newId = created ? updated.contacts.find((c) => !linked.has(c.id))?.id : undefined;
      onLinked(updated, newId);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      setBusy(false);
    }
  }

  const name = text.trim();
  const company = job.company && job.company !== 'Unknown company' ? job.company : undefined;
  return (
    <div className="picker">
      <div className="filter-row">
        <label className="field grow">
          <span>Find or create a contact</span>
          <input ref={inputRef} type="search" placeholder="Name, company or email" value={text}
            onChange={(e) => setText(e.target.value)} />
        </label>
        <label className="field">
          <span>Relation</span>
          <select value={relation} onChange={(e) => setRelation(e.target.value as Relation)}>
            {RELATIONS.map((r) => <option key={r} value={r}>{RELATION_LABELS[r]}</option>)}
          </select>
        </label>
        <button type="button" onClick={onCancel}>Cancel</button>
      </div>
      <ul className="picker-results" aria-label="Contacts">
        {results.filter((c) => !linked.has(c.id)).map((c) => (
          <li key={c.id}>
            <button type="button" disabled={busy}
              onClick={() => link(() => api.linkContact(job.key, { contact_id: c.id }, relation))}>
              <strong>{c.name}</strong>
              <span className="muted">{[c.role, c.company, c.email].filter(Boolean).join(' · ')}</span>
            </button>
          </li>
        ))}
        {name && (
          <li>
            <button type="button" className="create" disabled={busy}
              onClick={() => link(() => api.linkContact(job.key, { contact: { name, company } }, relation), true)}>
              Create '{name}'
            </button>
          </li>
        )}
      </ul>
      {error && <p className="error" role="alert">{error}</p>}
    </div>
  );
}

function ContactCard({ contact, startEditing, onSave, onUnlink }: {
  contact: JobContact;
  startEditing: boolean;
  onSave: (fields: Partial<JobContact>) => void;
  onUnlink: () => void;
}) {
  const [editing, setEditing] = useState(startEditing);
  const field = (label: string, key: 'name' | 'role' | 'email' | 'phone' | 'linkedin_url', type = 'text') => (
    <BlurField label={label} type={type} value={contact[key]}
      onSave={(v) => { if (key !== 'name' || v) onSave({ [key]: v }); }} />
  );
  return (
    <li className="contact-card">
      <div className="contact-head">
        <strong>{contact.name}</strong>
        <span className="chip">{RELATION_LABELS[contact.relation] ?? contact.relation}</span>
        {contact.role && <span className="muted">{contact.role}</span>}
        <span className="contact-actions">
          <button type="button" className="link-button" aria-expanded={editing} onClick={() => setEditing(!editing)}>
            {editing ? 'Done' : 'Edit'}
          </button>
          <button type="button" className="chip-remove" aria-label={`Remove ${contact.name}`} onClick={onUnlink}>×</button>
        </span>
      </div>
      <div className="link-row">
        {contact.email && <a href={`mailto:${contact.email}`}>{contact.email}</a>}
        {contact.phone && <a href={`tel:${contact.phone.replace(/[^\d+]/g, '')}`}>{contact.phone}</a>}
        {contact.linkedin_url && <a href={contact.linkedin_url} target="_blank" rel="noreferrer noopener">LinkedIn ↗</a>}
      </div>
      {editing ? (
        <div className="contact-edit">
          <div className="form-grid">
            {field('Name', 'name')}
            {field('Role', 'role')}
            {field('Email', 'email', 'email')}
            {field('Phone', 'phone', 'tel')}
            {field('LinkedIn', 'linkedin_url', 'url')}
          </div>
          <BlurField label="Notes" multiline value={contact.notes} onSave={(v) => onSave({ notes: v })} />
        </div>
      ) : (
        contact.notes && <p className="pre">{contact.notes}</p>
      )}
    </li>
  );
}

/** The job's contacts as cards, with a picker to link an existing contact or create one. */
export default function ContactsPanel({ job, onChange }: { job: JobDetail; onChange: (job: JobDetail) => void }) {
  const [picking, setPicking] = useState(false);
  const [newId, setNewId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function run(fn: () => Promise<JobDetail>) {
    setError(null);
    try {
      onChange(await fn());
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }

  return (
    <section className="card" aria-labelledby="contacts-title">
      <div className="card-head">
        <h2 id="contacts-title">Contacts</h2>
        {!picking && <button type="button" onClick={() => setPicking(true)}>Add contact</button>}
      </div>
      {picking && (
        <ContactPicker job={job} onCancel={() => setPicking(false)}
          onLinked={(updated, createdId) => {
            setPicking(false);
            setNewId(createdId ?? null);
            onChange(updated);
          }} />
      )}
      {job.contacts.length ? (
        <ul className="contact-list">
          {job.contacts.map((c) => (
            <ContactCard key={c.id} contact={c} startEditing={c.id === newId}
              onSave={(fields) => run(async () => {
                await api.updateContact(c.id, fields);
                return api.getJob(job.key);
              })}
              onUnlink={() => run(() => api.unlinkContact(job.key, c.id))} />
          ))}
        </ul>
      ) : (
        !picking && <p className="muted">No contacts yet: add the recruiter, a referral or your interviewers.</p>
      )}
      {error && <p className="error" role="alert">{error}</p>}
    </section>
  );
}
