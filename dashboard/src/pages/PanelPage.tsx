import { useEffect, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import * as api from '../api';
import Flower from '../components/Flower';
import { day } from '../labels';
import type { PanelGrader, PanelMode, PanelOverview, PanelReport, PanelRun, PanelWinner } from '../types';

const CLAUDE_URL = 'https://claude.ai/projects';
const MODE_LABELS: Record<PanelMode, string> = {
  latest_vs_previous: 'Latest draft vs the one before',
  rules_a_vs_b: 'Newest rules vs the rules before',
};
const MODEL_LABELS = { sonnet: 'Sonnet', opus: 'Opus' } as const;
const GRADER_LABELS: Record<PanelGrader, string> = { sonnet: 'Sonnet', opus: 'Opus', you: 'You' };

const pct = (v: number | null | undefined) => (v === null || v === undefined ? '–' : `${Math.round(v * 100)}%`);
const num = (v: number | null | undefined) => (v === null || v === undefined ? '–' : v.toFixed(1));
const errText = (err: unknown) => (err instanceof Error ? err.message : String(err));

/** Resume panel: blind A/B grading of two drafts per job in Claude Pro (Sonnet and Opus), plus your own picks. */
export default function PanelPage({ onChange }: { onChange?: () => void }) {
  const [params, setParams] = useSearchParams();
  const [overview, setOverview] = useState<PanelOverview | null>(null);
  const [run, setRun] = useState<PanelRun | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [n, setN] = useState(5);
  const [mode, setMode] = useState<PanelMode>('latest_vs_previous');
  const [creating, setCreating] = useState(false);
  const [copiedRubric, setCopiedRubric] = useState(false);
  const runId = params.get('run') ?? overview?.runs[0]?.id ?? null;

  useEffect(() => {
    const ctrl = new AbortController();
    api.getPanel(ctrl.signal).then(setOverview).catch((err) => {
      if (!ctrl.signal.aborted) setError(errText(err));
    });
    return () => ctrl.abort();
  }, []);

  useEffect(() => {
    if (!runId) return;
    const ctrl = new AbortController();
    api.getPanelRun(runId, ctrl.signal).then(setRun).catch((err) => {
      if (!ctrl.signal.aborted) setError(errText(err));
    });
    return () => ctrl.abort();
  }, [runId]);

  async function refreshOverview() {
    try {
      setOverview(await api.getPanel());
    } catch {
      // the run itself is already shown
    }
    onChange?.();
  }

  async function create() {
    setCreating(true);
    setError(null);
    try {
      const created = await api.createPanelRun(n, mode);
      setRun(created);
      setParams({ run: created.id });
      await refreshOverview();
    } catch (err) {
      setError(errText(err));
    } finally {
      setCreating(false);
    }
  }

  function updated(next: PanelRun) {
    const finished = next.status !== run?.status;
    setRun(next);
    if (finished) void refreshOverview();
  }

  async function copyRubric() {
    if (!overview) return;
    try {
      await api.copyText(overview.rubric.text);
      setCopiedRubric(true);
    } catch (err) {
      setError(errText(err));
    }
  }

  if (!overview) {
    return error ? <p className="notice error" role="alert">Couldn't load the panel: {error}</p> : (
      <div className="loader-state" aria-live="polite" aria-busy="true"><Flower variant="loader" size={64} />Loading…</div>
    );
  }

  const available = overview.comparable[mode];
  const short = Math.max(0, overview.needed - available);
  return (
    <article className="job-page panel-page">
      <h1>Resume panel</h1>
      <p className="muted">
        Check that the resume process is improving: two models grade two versions of your resume per job, blind, and
        you pick your own winners. Your picks are the ground truth; Claude tends to favor Claude-written text.
      </p>
      {overview.due && <p className="stale-note" role="status">Panel due: {overview.due}.</p>}
      {error && <p className="error" role="alert">{error}</p>}

      <details className="card">
        <summary><h2>One-time setup</h2></summary>
        <ol className="setup-steps">
          <li>
            On <a href={CLAUDE_URL} target="_blank" rel="noreferrer noopener">claude.ai ↗</a>, create a project named{' '}
            <strong>{overview.project_name}</strong> (separate from your resume project).
          </li>
          <li>
            Add the rubric to its knowledge:{' '}
            {overview.rubric.url
              ? <a href={overview.rubric.url} target="_blank" rel="noreferrer noopener">{overview.rubric.name} ↗</a>
              : <span className="muted">the Doc is written with the other Claude Docs (Claude setup); or paste it as text. </span>}
            {' '}<button type="button" onClick={copyRubric}>{copiedRubric ? 'Copied rubric' : 'Copy rubric'}</button>
          </li>
          <li>
            Project instructions:
            <p className="pre card">{overview.project_instructions}</p>
          </li>
        </ol>
      </details>

      <section className="card" aria-labelledby="panel-create">
        <h2 id="panel-create">1. Create a run</h2>
        <div className="inline-inputs">
          <label className="field"><span>Jobs</span>
            <input type="number" min={1} max={20} className="short" value={n}
              onChange={(e) => setN(Math.max(1, Math.min(20, Number(e.target.value) || 1)))} /></label>
          <label className="field"><span>Compare</span>
            <select value={mode} onChange={(e) => setMode(e.target.value as PanelMode)}>
              {(Object.keys(MODE_LABELS) as PanelMode[]).map((m) => <option key={m} value={m}>{MODE_LABELS[m]}</option>)}
            </select></label>
          <button type="button" className="primary" disabled={creating || available === 0} onClick={create}>
            {creating ? 'Creating…' : 'Create run'}
          </button>
        </div>
        <p className="muted small" data-testid="comparable">
          {available} {available === 1 ? 'job has' : 'jobs have'} two comparable drafts.
          {short > 0 && ` A panel needs ${overview.needed}: ${short} more ${short === 1 ? 'job needs' : 'jobs need'} a second draft.`}
        </p>
      </section>

      {run && (
        <>
          <p className="muted small">
            Run of {day(run.created_at)} · {run.items.length} items · {MODE_LABELS[run.mode]} · {run.status}
          </p>
          {run.packets.map((p) => (
            <ModelStep key={p.packet} run={run} packet={p} onUpdated={updated} />
          ))}
          <PicksStep run={run} onUpdated={updated} onError={setError} />
          {run.report && <Report report={run.report} />}
        </>
      )}

      {overview.runs.length > 1 && (
        <details className="card">
          <summary><h2>Earlier runs</h2></summary>
          <ul className="history">
            {overview.runs.map((r) => (
              <li key={r.id}>
                <button type="button" className="link-button" onClick={() => setParams({ run: r.id })}
                  aria-current={r.id === runId ? 'true' : undefined}>
                  {day(r.created_at)} · {MODE_LABELS[r.mode]} · {r.status}
                </button>
              </li>
            ))}
          </ul>
        </details>
      )}
    </article>
  );
}

function ModelStep({ run, packet, onUpdated }: {
  run: PanelRun; packet: PanelRun['packets'][number]; onUpdated: (run: PanelRun) => void;
}) {
  const [copied, setCopied] = useState<'ok' | 'failed' | null>(null);
  const [text, setText] = useState('');
  const [busy, setBusy] = useState(false);
  const [errors, setErrors] = useState<string[]>([]);
  const [message, setMessage] = useState<string | null>(null);
  const model = MODEL_LABELS[packet.model];
  const graded = run.graded[packet.model];

  async function copy() {
    try {
      await api.copyText(packet.prompt);
      setCopied('ok');
    } catch {
      setCopied('failed');
    }
  }

  async function submit() {
    setBusy(true);
    setErrors([]);
    setMessage(null);
    try {
      const out = await api.importPanelGrades(run.id, packet.model, text);
      if (out.ok) {
        setMessage(`${out.graded} items graded.`);
        setText('');
        onUpdated(out.run);
      } else {
        setErrors(out.errors);
      }
    } catch (err) {
      setErrors([errText(err)]);
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="card" aria-label={`Model ${packet.packet}: ${model}`}>
      <div className="card-head">
        <h2>{packet.packet + 1}. Model {packet.packet}: {model}</h2>
        {graded > 0 && <span className="muted">{graded} of {run.items.length} graded</span>}
      </div>
      <p className="muted small">
        Use {model}, in a new chat in the Panel project.{packet.packet === 2 && ' This packet shows each pair in the opposite order.'}
      </p>
      <div className="action-group">
        <button type="button" onClick={copy}>{copied === 'ok' ? 'Copied' : `Copy packet for model ${packet.packet}`}</button>
        <a href={CLAUDE_URL} target="_blank" rel="noreferrer noopener">claude.ai ↗</a>
      </div>
      {copied === 'failed' && <textarea readOnly rows={4} aria-label={`Packet ${packet.packet}`} value={packet.prompt} />}
      <label className="notes">
        <span>Paste {model}'s reply</span>
        <textarea rows={5} value={text} placeholder="Paste the whole reply, including the ```json block at the end."
          onChange={(e) => setText(e.target.value)} />
      </label>
      <div className="action-group">
        <button type="button" disabled={!text.trim() || busy} onClick={submit}>{busy ? 'Importing…' : 'Import grades'}</button>
      </div>
      {message && <p className="progress ok" role="status">{message}</p>}
      {errors.length > 0 && (
        <div className="check-errors" role="alert">
          <p><strong>Not imported.</strong></p>
          <ol aria-label="Problems">{errors.map((e, i) => <li key={i}>{e}</li>)}</ol>
        </div>
      )}
    </section>
  );
}

function PicksStep({ run, onUpdated, onError }: {
  run: PanelRun; onUpdated: (run: PanelRun) => void; onError: (message: string) => void;
}) {
  const [busy, setBusy] = useState<number | null>(null);
  const picked = run.items.filter((i) => i.your_pick).length;

  async function pick(item: number, winner: PanelWinner) {
    setBusy(item);
    try {
      onUpdated(await api.pickPanelItem(run.id, item, winner));
    } catch (err) {
      onError(errText(err));
    } finally {
      setBusy(null);
    }
  }

  return (
    <section className="card" aria-labelledby="panel-picks">
      <div className="card-head">
        <h2 id="panel-picks">4. Your picks</h2>
        <span className="muted">{picked} of {run.items.length} picked</span>
      </div>
      <p className="muted small">Read both versions and pick the one you'd send. The report appears after your last pick.</p>
      {run.items.map((item) => (
        <div key={item.item} className="panel-item" aria-label={`Item ${item.item}`} role="group">
          <h3>Item {item.item}: {item.title ?? 'Untitled role'} at {item.company}</h3>
          <div className="panel-pair">
            <div><strong>First</strong><p className="pre panel-resume">{item.first_text}</p></div>
            <div><strong>Second</strong><p className="pre panel-resume">{item.second_text}</p></div>
          </div>
          <div className="segmented small" role="group" aria-label={`Your pick for item ${item.item}`}>
            {(['first', 'second', 'tie'] as PanelWinner[]).map((w) => (
              <button key={w} type="button" aria-pressed={item.your_pick === w} disabled={busy === item.item} onClick={() => pick(item.item, w)}>
                {w === 'first' ? 'First' : w === 'second' ? 'Second' : 'Tie'}
              </button>
            ))}
          </div>
        </div>
      ))}
    </section>
  );
}

function Report({ report }: { report: PanelReport }) {
  const graders: PanelGrader[] = ['you', 'sonnet', 'opus'];
  return (
    <section className="card" aria-labelledby="panel-report">
      <h2 id="panel-report">5. Report</h2>
      <p data-testid="verdict"><strong>Verdict: {report.verdict ?? 'waiting for your picks'}</strong></p>
      <div className="table-scroll">
        <table className="jobs-table panel-table">
          <thead>
            <tr><th>Grader</th><th className="num">Graded</th><th className="num">Newer wins</th>
              <th className="num">Grade older → newer</th><th className="num">Required met</th>
              <th className="num">Clarity</th><th className="num">Red flags</th></tr>
          </thead>
          <tbody>
            {graders.map((g) => {
              const s = report.graders[g];
              return (
                <tr key={g}>
                  <td>{GRADER_LABELS[g]}</td>
                  <td className="num">{s.graded}</td>
                  <td className="num">{pct(s.newer_win_rate)}</td>
                  <td className="num">{s.a && s.b ? `${num(s.a.mean_overall)} → ${num(s.b.mean_overall)}` : '–'}</td>
                  <td className="num">{s.a && s.b ? `${pct(s.a.met_rate.required)} → ${pct(s.b.met_rate.required)}` : '–'}</td>
                  <td className="num">{s.a && s.b ? `${num(s.a.mean_clarity)} → ${num(s.b.mean_clarity)}` : '–'}</td>
                  <td className="num">{s.a && s.b ? `${s.a.red_flags} → ${s.b.red_flags}` : '–'}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <p className="muted small">Grades are A=4 … D=1; ties count as half a win.</p>
      <h3>Agreement</h3>
      <ul aria-label="Agreement">
        <li>Sonnet vs Opus: {pct(report.agreement.sonnet_vs_opus)}</li>
        <li>Sonnet vs you: {pct(report.agreement.sonnet_vs_you)}</li>
        <li>Opus vs you: {pct(report.agreement.opus_vs_you)}</li>
      </ul>
      <h3>Items</h3>
      <ol aria-label="Items">
        {report.items.map((i) => (
          <li key={i.item}>
            {i.title} at {i.company} (v{i.older_version} vs v{i.newer_version}, newer shown {i.newer_shown}):{' '}
            {graders.map((g) => `${GRADER_LABELS[g]} ${i.winners[g] ?? '–'}`).join(' · ')}
          </li>
        ))}
      </ol>
      {report.heard_back.length > 0 && (
        <>
          <h3>Heard back by rules version</h3>
          <ul aria-label="Heard back">
            {report.heard_back.map((h) => (
              <li key={h.rules_version}>
                rules {h.rules_version}: {h.heard_back} of {h.submitted} heard back
                {h.rate === null ? ` (${h.note})` : ` (${pct(h.rate)})`}
              </li>
            ))}
          </ul>
        </>
      )}
    </section>
  );
}
