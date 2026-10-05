import { useEffect, useRef, useState } from 'react';
import { getTask } from '../api';
import type { Task } from '../types';

export const POLL_MS = 3000;

const KIND_LABELS: Record<Task['kind'], string> = {
  resume: 'resume',
  cover: 'cover letter',
  both: 'resume and cover letter',
};

function elapsed(from: string | null, to?: string | null): string {
  if (!from) return '';
  const ms = (to ? Date.parse(to) : Date.now()) - Date.parse(from);
  if (!Number.isFinite(ms) || ms < 0) return '';
  const s = Math.round(ms / 1000);
  return s < 60 ? `${s}s` : `${Math.floor(s / 60)}m ${s % 60}s`;
}

/** Polls a generation task every POLL_MS until it's done or failed, then calls onFinish once. */
export default function TaskProgress({ task: initial, onFinish }: { task: Task; onFinish: (task: Task) => void }) {
  const [task, setTask] = useState(initial);
  const finished = useRef(false);
  const onFinishRef = useRef(onFinish);
  onFinishRef.current = onFinish;

  useEffect(() => {
    setTask(initial);
    finished.current = initial.state === 'done' || initial.state === 'error';
    if (finished.current) return;
    let stop = false;
    const ctrl = new AbortController();
    const timer = setInterval(() => {
      getTask(initial.id, ctrl.signal)
        .then((t) => {
          if (stop) return;
          setTask(t);
          if ((t.state === 'done' || t.state === 'error') && !finished.current) {
            finished.current = true;
            clearInterval(timer);
            onFinishRef.current(t);
          }
        })
        .catch(() => {}); // a missed poll is retried on the next tick
    }, POLL_MS);
    return () => {
      stop = true;
      clearInterval(timer);
      ctrl.abort();
    };
  }, [initial]);

  const what = KIND_LABELS[task.kind];
  let text: string;
  let cls = 'progress';
  if (task.state === 'queued') text = `Queued: ${what}…`;
  else if (task.state === 'running') text = `Generating ${what}… ${elapsed(task.started_at)}`;
  else if (task.state === 'done') {
    text = `Generated ${what} in ${elapsed(task.started_at, task.finished_at)}.`;
    cls += ' ok';
  } else {
    text = `Couldn't generate the ${what}: ${task.error ?? 'unknown error'}`;
    cls += ' error';
  }
  return (
    <p className={cls} role="status" aria-live="polite" data-state={task.state}>
      {(task.state === 'queued' || task.state === 'running') && <span className="spinner" aria-hidden="true" />}
      {text}
    </p>
  );
}
