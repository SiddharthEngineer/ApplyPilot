import { useEffect, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import * as api from '../api';
import Flower from '../components/Flower';
import type { ClaudeSetup } from '../types';

// The ?drive= outcome the Drive connect callback (web/auth.py) redirects back with.
const DRIVE_OUTCOMES: Record<string, { text: string; error: boolean }> = {
  connected: { text: 'Google Drive is connected.', error: false },
  denied: { text: "Google Drive wasn't connected: allow access to the files Strelitzia creates and try again.", error: true },
  error: { text: "Couldn't connect Google Drive. Try again.", error: true },
  unavailable: { text: "This server can't connect Google Drive yet.", error: true },
};

/** One-time setup: a Claude Project with the two Strelitzia Docs attached and the project instructions pasted in. */
export default function ClaudeSetupPage() {
  const [params, setParams] = useSearchParams();
  const outcome = DRIVE_OUTCOMES[params.get('drive') ?? ''];
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

  async function disconnect() {
    setBusy(true);
    setError(null);
    try {
      await api.disconnectDrive();
      setParams({}, { replace: true });  // the ?drive= outcome no longer applies
      setSetup(await api.getClaudeSetup());
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
      {outcome && <p className={outcome.error ? 'notice error' : 'notice'} role="status">{outcome.text}</p>}
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
          {!setup.drive && setup.connect_url && (
            <a className="button" href={setup.connect_url}>Connect Google Drive</a>
          )}
          {setup.drive && (
            <button type="button" disabled={busy} onClick={publish}>
              {busy ? 'Writing…' : missing ? 'Write the Docs to Drive' : 'Refresh the Docs'}
            </button>
          )}
          {setup.drive_source === 'user' && (
            <p className="muted small">
              Drive connected: files go to a Strelitzia folder in your Drive ({setup.drive_detail}).{' '}
              {setup.connect_url && <a href={setup.connect_url}>Reconnect</a>}{' '}
              <button type="button" disabled={busy} onClick={disconnect}>Disconnect</button>
            </p>
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
