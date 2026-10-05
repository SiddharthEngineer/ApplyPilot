import { Link, useNavigate } from 'react-router-dom';
import { day, roleLabel, WORK_MODE_LABELS } from '../labels';
import type { JobListItem, Sort, SortKey } from '../types';
import StatusBadge from './StatusBadge';

interface Props {
  items: JobListItem[];
  sort: Sort;
  onSort: (sort: Sort) => void;
}

function SortHeader({ label, column, sort, onSort }: { label: string; column: SortKey; sort: Sort; onSort: Props['onSort'] }) {
  const active = sort.replace(/^-/, '') === column;
  const desc = sort.startsWith('-');
  // First click on a column sorts descending (newest / highest first); clicking again flips it.
  const next: Sort = active && desc ? column : `-${column}`;
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
              <td className="num">{job.fit_score ?? '—'}</td>
              <td className="nowrap">{day(job.discovered_at)}</td>
              <td className="nowrap">{day(job.deadline)}</td>
              <td className="num">{job.status === 'submitted' ? (job.days_since_submitted ?? '—') : ''}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
