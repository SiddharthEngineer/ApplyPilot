import { useState, type FormEvent } from 'react';
import type { ContactFields } from '../types';

export type NewContact = ContactFields & { name: string };
type Key = 'name' | 'email' | 'phone' | 'linkedin_url' | 'company' | 'role' | 'notes';

/** The same checks as the API: an email has an @, a LinkedIn link is http(s) on linkedin.com. */
export function contactProblem(fields: NewContact): string | null {
  if (!fields.name.trim()) return 'Name is required.';
  if (fields.email && !fields.email.includes('@')) return 'Email must contain @.';
  if (fields.linkedin_url) {
    let ok = false;
    try { // the shape check keeps the browser's URL parser and the API's urlparse on the same host
      const url = new URL(fields.linkedin_url);
      const host = url.hostname.toLowerCase();
      ok = /^https?:\/\/[^\\\s]+$/i.test(fields.linkedin_url)
        && (host === 'linkedin.com' || host.endsWith('.linkedin.com'));
    } catch {
      ok = false;
    }
    if (!ok) return 'LinkedIn must be an http(s) link on linkedin.com.';
  }
  return null;
}

/** A form for a new contact: Name (required), Email, Phone, LinkedIn, then Company, Role and Notes. */
export default function ContactForm({ initial = {}, submitLabel = 'Save contact', onSubmit, onCancel }: {
  initial?: ContactFields;
  submitLabel?: string;
  onSubmit: (fields: NewContact) => Promise<void>;
  onCancel: () => void;
}) {
  const [values, setValues] = useState<Record<Key, string>>({
    name: initial.name ?? '', email: initial.email ?? '', phone: initial.phone ?? '',
    linkedin_url: initial.linkedin_url ?? '', company: initial.company ?? '', role: initial.role ?? '',
    notes: initial.notes ?? '',
  });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(e: FormEvent) {
    e.preventDefault();
    const fields: NewContact = { name: values.name.trim() };
    for (const key of ['email', 'phone', 'linkedin_url', 'company', 'role', 'notes'] as const) {
      if (values[key].trim()) fields[key] = values[key].trim();
    }
    const problem = contactProblem(fields);
    setError(problem);
    if (problem) return;
    setBusy(true);
    try {
      await onSubmit(fields);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  const input = (label: string, key: Key, type = 'text', placeholder?: string) => (
    <label className="field"><span>{label}</span>
      <input type={type} value={values[key]} placeholder={placeholder} required={key === 'name'}
        onChange={(e) => setValues({ ...values, [key]: e.target.value })} /></label>
  );
  return (
    <form className="contact-form" aria-label="New contact" noValidate onSubmit={submit}>
      <div className="form-grid">
        {input('Name', 'name')}
        {input('Email', 'email', 'email')}
        {input('Phone', 'phone', 'tel')}
        {input('LinkedIn', 'linkedin_url', 'url', 'https://www.linkedin.com/in/…')}
        {input('Company', 'company')}
        {input('Role', 'role')}
      </div>
      <label className="field"><span>Notes</span>
        <textarea rows={3} value={values.notes} onChange={(e) => setValues({ ...values, notes: e.target.value })} /></label>
      {error && <p className="error" role="alert">{error}</p>}
      <div className="dialog-actions">
        <button type="submit" className="primary" disabled={busy}>{submitLabel}</button>
        <button type="button" disabled={busy} onClick={onCancel}>Cancel</button>
      </div>
    </form>
  );
}
