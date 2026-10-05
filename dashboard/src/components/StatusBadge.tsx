import { STATUS_LABELS } from '../labels';
import type { Status, StatusColor } from '../types';

export default function StatusBadge({ status, color }: { status: Status; color: StatusColor }) {
  return (
    <span className={`badge badge-${color}`} data-color={color}>
      {STATUS_LABELS[status] ?? status}
    </span>
  );
}
