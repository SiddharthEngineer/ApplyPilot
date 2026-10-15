// Readable labels for the API's enum values.
import type { Importance, Outcome, Relation, Status, StatusFilter } from './types';

export const STATUS_LABELS: Record<Status | 'no_response', string> = {
  active: 'Active',
  inactive: 'Inactive',
  in_progress: 'In progress',
  submitted: 'Submitted',
  rejected: 'Rejected',
  heard_back: 'Heard back',
  no_response: 'No response',
};

// Order of the status filter's segments ("all" first).
export const STATUS_FILTERS: StatusFilter[] = [
  'all', 'active', 'inactive', 'in_progress', 'submitted', 'no_response', 'rejected', 'heard_back',
];

export const ROLE_LABELS: Record<string, string> = {
  data_science: 'Data Science',
  data_engineering: 'Data Engineering',
  ml_ai_engineering: 'ML/AI Engineering',
  data_analytics_bi: 'Data/BI Analytics',
  software_engineering: 'Software Engineering',
  research_science: 'Research Science',
  product_program_management: 'Product/Program Mgmt',
  quant_finance: 'Quant/Finance',
  hardware_electrical: 'Hardware/EE',
  it_infra_devops: 'IT/Infra/DevOps',
  other: 'Other',
};

export const WORK_MODE_LABELS: Record<string, string> = {
  remote: 'Remote',
  hybrid: 'Hybrid',
  onsite: 'On-site',
  unknown: 'Unknown',
};

export const IMPORTANCES: Importance[] = ['dont_care', 'nice', 'important', 'very_important', 'dealbreaker'];

export const IMPORTANCE_LABELS: Record<Importance, string> = {
  dont_care: "Don't care",
  nice: 'Nice to have',
  important: 'Important',
  very_important: 'Very important',
  dealbreaker: 'Dealbreaker',
};

/** "snake_case" enum value -> "Snake case", for fields without a label table. */
export function humanize(value: string | null | undefined): string {
  if (!value) return '';
  const text = value.replace(/_/g, ' ');
  return text.charAt(0).toUpperCase() + text.slice(1);
}

export function roleLabel(value: string | null | undefined): string {
  return value ? (ROLE_LABELS[value] ?? humanize(value)) : '—';
}

/** The YYYY-MM-DD part of an ISO date or timestamp. */
export function day(value: string | null | undefined): string {
  return value ? value.slice(0, 10) : '—';
}

/** Today's date as YYYY-MM-DD in the browser's time zone. */
export function todayIso(): string {
  const now = new Date();
  const pad = (n: number) => String(n).padStart(2, '0');
  return `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}`;
}

export const RELATION_LABELS: Record<Relation, string> = {
  recruiter: 'Recruiter',
  hiring_manager: 'Hiring manager',
  referral: 'Referral',
  interviewer: 'Interviewer',
  other: 'Other',
};

export const OUTCOME_LABELS: Record<Outcome, string> = {
  pending: 'Pending',
  passed: 'Passed',
  rejected: 'Rejected',
  withdrew: 'Withdrew',
};

/** An ISO timestamp as the browser's local date and time ("Oct 18, 2026, 2:00 PM"). */
export function localDateTime(value: string | null | undefined): string {
  if (!value) return '—';
  const d = new Date(value);
  return Number.isNaN(d.getTime()) ? value : d.toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' });
}

/** An ISO timestamp as the browser's local date ("Oct 18, 2026"). */
export function localDate(value: string | null | undefined): string {
  if (!value) return '—';
  const d = new Date(value);
  return Number.isNaN(d.getTime()) ? value : d.toLocaleDateString(undefined, { dateStyle: 'medium' });
}
