import { useEffect, useState } from 'react';
import * as api from '../api';
import Flower from '../components/Flower';
import type { ClaudeSetup } from '../types';

/** One-time setup: a Claude Project with the two Strelitzia Docs attached and the project instructions pasted in. */
export default function ClaudeSetupPage() {
  const [setup, setSetup] = useState<ClaudeSetup | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    const ctrl = new AbortController();
    api.getClaudeSetup(ctrl.signal).then(setSetup).catch((err) => {
      if (!ctrl.signal.aborted) setError(err.message || String(err));
    });
    return () => ctrl.abort();
  }, []);

  async function publish() {
    setBusy(true);
    setError(null);
    try {
      setSetup(await api.publishClaudeDocs());
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  async function copyInstructions() {
    if (!setup) return;
    try {
      await api.copyText(setup.project_instructions);
      setCopied(true);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }

  if (!setup) {
    return error ? <p className="notice error" role="alert">Couldn't load the Claude setup: {error}</p> : (
      <div className="loader-state" aria-live="polite" aria-busy="true"><Flower variant="loader" size={64} />Loading…</div>
    );
  }

  const missing = setup.docs.some((d) => !d.url);
  return (
    <article className="job-page claude-setup">
      <h1>Claude setup</h1>
      <p className="muted">
        Resumes and cover letters are written in your Claude Pro plan. Do this once; after that every job's
        <strong> Open in Claude</strong> button gives you a prompt for a new chat in the project.
      </p>
      {error && <p className="error" role="alert">{error}</p>}
      <ol className="setup-steps">
        <li>
          On <a href="https://claude.ai/projects" target="_blank" rel="noreferrer noopener">claude.ai ↗</a>, create a
          project named <strong>{setup.project_name}</strong>.
        </li>
        <li>
          Add these Google Docs to the project's knowledge (Add content → Google Drive):
          {setup.drive ? (
            <ul aria-label="Project Docs">
              {setup.docs.map((d) => (
                <li key={d.kind}>
                  {d.url ? <a href={d.url} target="_blank" rel="noreferrer noopener">{d.name} ↗</a> : <span>{d.name} (not written yet)</span>}
                  {d.changed_at && <span className="muted"> · last changed {d.changed_at.slice(0, 16).replace('T', ' ')}</span>}
                </li>
              ))}
            </ul>
          ) : (
            <p className="muted">
              Google Drive isn't set up ({setup.drive_detail}). Without it, Open in Claude copies the rules and your
              content library with every job, so you can skip this step.
            </p>
          )}
          {setup.drive && (
            <button type="button" disabled={busy} onClick={publish}>
              {busy ? 'Writing…' : missing ? 'Write the Docs to Drive' : 'Refresh the Docs'}
            </button>
          )}
        </li>
        <li>
          Paste these project instructions (Project → Instructions):
          <p className="pre card">{setup.project_instructions}</p>
          <button type="button" onClick={copyInstructions}>{copied ? 'Copied' : 'Copy instructions'}</button>
        </li>
      </ol>
      <p className="muted small">
        Rules version {setup.rules_version}. The app updates the Docs when the rules or your library change; the
        project picks the change up on its own.
      </p>
    </article>
  );
}
