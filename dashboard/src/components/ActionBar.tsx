import { useState } from 'react';
import { todayIso } from '../labels';
import type { JobDetail, UserStatus } from '../types';

interface Props {
  job: JobDetail;
  busy: boolean; // a request from this page is in flight
  onStatus: (status: UserStatus | null, submittedAt?: string) => void;
}

export default function ActionBar({ job, busy, onStatus }: Props) {
  const [submitting, setSubmitting] = useState(false);
  const [date, setDate] = useState(todayIso);

  return (
    <div className="action-bar">
      <div className="action-group" role="group" aria-label="Status">
        {submitting ? (
          <form
            className="submit-form"
            onSubmit={(e) => {
              e.preventDefault();
              onStatus('submitted', date || undefined);
              setSubmitting(false);
            }}
          >
            <label>
              Submitted on{' '}
              <input type="date" aria-label="Submitted on" value={date} max={todayIso()} onChange={(e) => setDate(e.target.value)} required />
            </label>
            <button type="submit" className="primary" disabled={busy}>Save</button>
            <button type="button" onClick={() => setSubmitting(false)}>Cancel</button>
          </form>
        ) : (
          <button
            type="button"
            disabled={busy || job.status === 'submitted'}
            onClick={() => {
              setDate(todayIso());
              setSubmitting(true);
            }}
          >
            Mark submitted
          </button>
        )}
        <button type="button" disabled={busy || job.status === 'rejected'} onClick={() => onStatus('rejected')}>
          Rejected
        </button>
        <button type="button" disabled={busy || job.status === 'heard_back'} onClick={() => onStatus('heard_back')}>
          Heard back
        </button>
        <button
          type="button"
          disabled={busy || job.status === 'active' || job.status === 'inactive'}
          title="Back to Active/Inactive (from the deadline)"
          onClick={() => onStatus(null)}
        >
          Reset
        </button>
      </div>
    </div>
  );
}
