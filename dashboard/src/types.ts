// Mirrors the dashboard API responses (src/applypilot/web/app.py, queries.py, tasks.py).

export type Status = 'active' | 'inactive' | 'in_progress' | 'submitted' | 'rejected' | 'heard_back';
export type StatusFilter = Status | 'no_response' | 'all';
export type UserStatus = 'in_progress' | 'submitted' | 'rejected' | 'heard_back';
export type StatusColor = 'grey' | 'orange' | 'yellow' | 'blue' | 'red' | 'green';
export type SortKey = 'discovered_at' | 'deadline' | 'fit_score';
export type Sort = SortKey | `-${SortKey}`;
export type WorkMode = 'remote' | 'hybrid' | 'onsite' | 'unknown';

export interface JobListItem {
  key: string;
  title: string | null;
  company: string | null;
  role_category: string | null;
  location: string | null;
  work_mode: WorkMode | null;
  fit_score: number | null;
  discovered_at: string | null;
  deadline: string | null;
  status: Status;
  status_color: StatusColor;
  days_since_submitted: number | null;
}

export interface JobList {
  total: number;
  page: number;
  page_size: number;
  items: JobListItem[];
}

export interface JobQuery {
  status?: StatusFilter[];
  role?: string[];
  work_mode?: string[];
  location?: string;
  found_from?: string;
  found_to?: string;
  due_from?: string;
  due_to?: string;
  score_min?: number;
  score_max?: number;
  q?: string;
  sort?: Sort;
  page?: number;
  page_size?: number;
}

export interface FacetCount {
  value: string;
  count: number;
}

export interface Facets {
  total: number;
  status: (FacetCount & { value: Status | 'no_response'; color: StatusColor | null })[];
  role_category: FacetCount[];
  work_mode: FacetCount[];
  location: FacetCount[];
}

export interface PostingLocation {
  city: string | null;
  state: string | null;
  country: string | null;
}

// The extract stage's JobPosting (enrichment/posting_model.py).
export interface JobDetails {
  company: string | null;
  title_normalized: string | null;
  role_category?: string;
  seniority: string;
  employment_type: string;
  work_mode: string;
  locations: PostingLocation[];
  remote_region: string | null;
  salary_min: number | null;
  salary_max: number | null;
  salary_currency: string | null;
  salary_period: string | null;
  posted_date: string | null;
  application_deadline: string | null;
  start_date: string | null;
  summary: string;
  team: string | null;
  responsibilities: string[];
  required_qualifications: string[];
  preferred_qualifications: string[];
  skills_required: string[];
  skills_preferred: string[];
  education_level: string;
  education_fields: string[];
  years_experience_min: number | null;
  years_experience_max: number | null;
  visa_sponsorship: string;
  security_clearance: string;
  travel: string | null;
  benefits: string[];
  company_blurb: string | null;
}

export interface StatusEvent {
  from_status: string | null;
  to_status: string | null;
  at: string;
  source: string | null;
}

export interface JobLinks {
  posting: string;
  apply: string | null;
  drive_folder: string | null;
  resume: string | null;
  cover_letter: string | null;
}

export interface JobDetail {
  key: string;
  url: string;
  title: string | null;
  company: string | null;
  site: string | null;
  location: string | null;
  work_mode: WorkMode | null;
  role_category: string | null;
  fit_score: number | null;
  score_reasoning: string | null;
  discovered_at: string | null;
  deadline: string | null;
  notes: string | null;
  description_text: string | null;
  details: JobDetails | null;
  status: Status;
  status_color: StatusColor;
  days_since_submitted: number | null;
  submitted_date: string | null;
  events: StatusEvent[];
  links: JobLinks;
}

export type TaskKind = 'resume' | 'cover' | 'both';
export type TaskState = 'queued' | 'running' | 'done' | 'error';

export interface Task {
  id: string;
  url: string;
  kind: TaskKind;
  state: TaskState;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  error: string | null;
  key: string | null;
}
