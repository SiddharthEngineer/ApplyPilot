import { useState } from 'react';
import { Link } from 'react-router-dom';
import * as api from '../api';
import { day } from '../labels';
import type { JobDetail, PacketResult } from '../types';
import CoveragePanel from './CoveragePanel';

const CLAUDE_URL = 'https://claude.ai/projects';

interface Props {
  job: JobDetail;
  onSaved: (job: JobDetail) => void;
}

type Check =
  | { state: 'idle' }
  | { state: 'checking' }
  | { state: 'failed'; errors: string[]; warnings: string[]; fixPrompt: string; copied: boolean }
  | { state: 'saved'; version: number; warnings: string[] };

/** Resume & cover letter: open the job in Claude, paste the reply back, and see the saved files and coverage. */
export default function WorkbenchPanel({ job, onSaved }: Props) {
  const [packet, setPacket] = useState<PacketResult | null>(null);
  const [copyFailed, setCopyFailed] = useState(false);
  const [opening, setOpening] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [text, setText] = useState('');
  const [check, setCheck] = useState<Check>({ state: 'idle' });

  async function openInClaude() {
    setOpening(true);
    setError(null);
    setCopyFailed(false);
    try {
      const p = await api.openPacket(job.key);
      setPacket(p);
      try {
        await api.copyText(p.prompt);
      } catch {
        setCopyFailed(true);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setOpening(false);
    }
  }

  async function checkAndSave() {
    setCheck({ state: 'checking' });
    setError(null);
    try {
      const out = await api.pasteResult(job.key, text);
      if (out.ok) {
        setCheck({ state: 'saved', version: out.outcome.draft_version, warnings: out.outcome.warnings });
        setText('');
        onSaved(out.job);
      } else {
        setCheck({ state: 'failed', errors: out.errors, warnings: out.warnings, fixPrompt: out.fix_prompt, copied: false });
      }
    } catch (err) {
      setCheck({ state: 'idle' });
      setError(err instanceof Error ? err.message : String(err));
    }
  }

  async function copyFix() {
    if (check.state !== 'failed') return;
    try {
      await api.copyText(check.fixPrompt);
      setCheck({ ...check, copied: true });
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }

  // A PDF still on the server is the newest draft (a successful Drive upload removes it), so it wins over Drive.
  const files: [string, string | null | undefined][] = [
    ['Resume PDF', job.links.resume_pdf || job.links.resume],
    ['Resume DOCX', job.links.resume_docx],
    ['Cover letter PDF', job.links.cover_letter_pdf || job.links.cover_letter],
  ];
  const shownFiles = files.filter(([, href]) => href);
  const history = job.draft_history ?? [];

  return (
    <section className="card workbench" aria-label="Resume & cover letter">
      <div className="card-head">
        <h2>Resume &amp; cover letter</h2>
        {job.draft_version ? <span className="muted">Draft v{job.draft_version}</span> : null}
      </div>
      <p className="muted small">
        Resumes are written in your Claude project. Not set up yet? See <Link to="/claude-setup">Claude setup</Link>.
      </p>

      <div className="action-group">
        <button type="button" className="primary" disabled={opening} onClick={openInClaude}>
          {opening ? 'Preparing…' : 'Open in Claude'}
        </button>
      </div>
      {packet && (
        <div className="progress ok" role="status">
          <span>
            {copyFailed ? 'Couldn\'t copy: select the prompt below and copy it. '
              : packet.inline ? 'Copied the full packet (Drive isn\'t set up). ' : 'Copied the prompt. '}
            Paste it into a new chat in your project.{' '}
            {packet.doc_url && <><a href={packet.doc_url} target="_blank" rel="noreferrer noopener">Packet Doc ↗</a>{' · '}</>}
            <a href={CLAUDE_URL} target="_blank" rel="noreferrer noopener">claude.ai ↗</a>
            {' · '}rules {packet.rules_version}
          </span>
        </div>
      )}
      {packet?.drive_error && <p className="error small">{packet.drive_error}</p>}
      {packet && copyFailed && <textarea readOnly rows={4} aria-label="Prompt" value={packet.prompt} />}

      <label className="notes">
        <span>Paste result</span>
        <textarea rows={6} value={text} placeholder="Paste Claude's whole reply, including the ```json block at the end."
          onChange={(e) => setText(e.target.value)} />
      </label>
      <div className="action-group">
        <button type="button" disabled={!text.trim() || check.state === 'checking'} onClick={checkAndSave}>
          {check.state === 'checking' ? 'Checking…' : 'Check & save'}
        </button>
      </div>
      {error && <p className="error" role="alert">{error}</p>}
      {check.state === 'failed' && (
        <div className="check-errors" role="alert">
          <p><strong>Not saved.</strong> Fix these in the same chat:</p>
          <ol aria-label="Problems">{check.errors.map((e, i) => <li key={i}>{e}</li>)}</ol>
          <button type="button" onClick={copyFix}>{check.copied ? 'Copied fix message' : 'Copy fix message'}</button>
        </div>
      )}
      {check.state === 'saved' && (
        <p className="progress ok" role="status">Saved draft v{check.version}.</p>
      )}
      {(check.state === 'saved' || check.state === 'failed') && check.warnings.length > 0 && (
        <ul className="muted small" aria-label="Warnings">{check.warnings.map((w, i) => <li key={i}>{w}</li>)}</ul>
      )}

      {shownFiles.length > 0 && (
        <nav className="link-row" aria-label="Files">
          {shownFiles.map(([label, href]) => (
            <a key={label} href={href!} target="_blank" rel="noreferrer noopener">{label} ↗</a>
          ))}
        </nav>
      )}
      {job.notes_for_user && <p className="muted small">Claude's note: {job.notes_for_user}</p>}
      {job.coverage && <CoveragePanel coverage={job.coverage} unmet={job.unmet ?? []} />}
      {history.length > 0 && (
        <details>
          <summary>Draft history</summary>
          <ol className="history" aria-label="Draft history">
            {history.slice().reverse().map((h) => (
              <li key={h.version}>
                v{h.version} · {day(h.at)} · rules {h.rules_version}
                {job.submitted_draft_version === h.version && <strong> · submitted</strong>}
              </li>
            ))}
          </ol>
        </details>
      )}
    </section>
  );
}
