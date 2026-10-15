import { Link, useNavigate } from 'react-router-dom';
import { day, localDate, roleLabel, WORK_MODE_LABELS } from '../labels';
import type { JobListItem, Sort, SortKey } from '../types';
import FitBar from './FitBar';
import StatusBadge from './StatusBadge';

const WEEK_MS = 7 * 24 * 3600 * 1000;

/** The next interview's local date, with a badge when it's within a week. */
function NextInterview({ at }: { at: string | null }) {
  if (!at) return <>—</>;
  const ms = Date.parse(at) - Date.now();
  return (
    <>
      {localDate(at)}
      {ms >= 0 && ms <= WEEK_MS && <span className="tag tag-soon">Soon</span>}
    </>
  );
}

interface Props {
  items: JobListItem[];
  sort: Sort;
  onSort: (sort: Sort) => void;
}

function SortHeader({ label, column, sort, onSort, ascFirst = false }: {
  label: string; column: SortKey; sort: Sort; onSort: Props['onSort']; ascFirst?: boolean;
}) {
  const active = sort.replace(/^-/, '') === column;
  const desc = sort.startsWith('-');
  // First click on a column sorts descending (newest / highest first), or ascending with ascFirst (soonest first);
  // clicking again flips it.
  const next: Sort = ascFirst ? (active && !desc ? `-${column}` : column) : (active && desc ? column : `-${column}`);
  return (
    <th aria-sort={active ? (desc ? 'descending' : 'ascending') : 'none'}>
      <button type="button" className="sort-header" onClick={() => onSort(next)}>
        {label}
        <span className="sort-indicator" aria-hidden="true">{active ? (desc ? '▼' : '▲') : '↕'}</span>
      </button>
    </th>
  );
}

export default function JobsTable({ items, sort, onSort }: Props) {
  const navigate = useNavigate();
  return (
    <div className="table-scroll">
      <table className="jobs-table">
        <thead>
          <tr>
            <th>Status</th>
            <th>Title</th>
            <th>Company</th>
            <th>Role</th>
            <th>Location</th>
            <SortHeader label="Fit" column="fit_score" sort={sort} onSort={onSort} />
            <SortHeader label="Found" column="discovered_at" sort={sort} onSort={onSort} />
            <SortHeader label="Due" column="deadline" sort={sort} onSort={onSort} />
            <SortHeader label="Next interview" column="next_interview" sort={sort} onSort={onSort} ascFirst />
            <th title="Days since submitted">Days</th>
          </tr>
        </thead>
        <tbody>
          {items.map((job) => (
            <tr
              key={job.key}
              className="job-row"
              onClick={(e) => {
                // Let real links (and modified clicks) behave normally.
                if ((e.target as HTMLElement).closest('a') || e.metaKey || e.ctrlKey) return;
                navigate(`/jobs/${job.key}`);
              }}
            >
              <td><StatusBadge status={job.status} color={job.status_color} /></td>
              <td className="col-title"><Link to={`/jobs/${job.key}`}>{job.title || '(untitled)'}</Link></td>
              <td>{job.company || '—'}</td>
              <td>{roleLabel(job.role_category)}</td>
              <td>
                {job.location || '—'}
                {(job.work_mode === 'remote' || job.work_mode === 'hybrid') && (
                  <span className={`tag tag-${job.work_mode}`}>{WORK_MODE_LABELS[job.work_mode]}</span>
                )}
              </td>
              <td className="num fit-cell">
                {job.fit_flags.length > 0 && (
                  <span className="flag" title={job.flag_reasons.join('\n')}
                    aria-label={`Flagged: ${job.flag_reasons.join('; ')}`}>⚠</span>
                )}
                <FitBar score={job.fit_score} stale={job.assessment_stale} />
              </td>
              <td className="nowrap">{day(job.discovered_at)}</td>
              <td className="nowrap">{day(job.deadline)}</td>
              <td className="nowrap"><NextInterview at={job.next_interview} /></td>
              <td className="num">{job.status === 'submitted' ? (job.days_since_submitted ?? '—') : ''}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
