// Mirrors the dashboard API responses (src/strelitzia/web/app.py, queries.py, tasks.py).

export type Status = 'active' | 'inactive' | 'in_progress' | 'submitted' | 'rejected' | 'heard_back';
export type StatusFilter = Status | 'no_response' | 'all';
export type UserStatus = 'in_progress' | 'submitted' | 'rejected' | 'heard_back';
export type StatusColor = 'grey' | 'orange' | 'yellow' | 'blue' | 'red' | 'green';
export type SortKey = 'discovered_at' | 'deadline' | 'fit_score' | 'next_interview';
export type Sort = SortKey | `-${SortKey}`;
export type WorkMode = 'remote' | 'hybrid' | 'onsite' | 'unknown';
export type Source = 'manual' | 'discovered';
export type IntakeStatus = 'queued' | 'scraping' | 'ok' | 'needs_paste' | 'error';

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
  source: Source;
  added_at: string | null;
  intake_status: IntakeStatus | null;
  next_interview: string | null;
  fit_breakdown: FitComponent[];
  fit_flags: string[];
  flag_reasons: string[];
  assessment_stale: boolean;
}

export type Importance = 'dont_care' | 'nice' | 'important' | 'very_important' | 'dealbreaker';

// One scored criterion of the fit score (scoring/preferences.py Component). score is 0-1, null when not set.
export interface FitComponent {
  key: string;
  label: string;
  score: number | null;
  importance: Importance;
  dealbreaker_hit: boolean;
  note: string;
}

export interface Rating {
  score: number; // 1-5
  evidence: string;
}

// The Claude assessment (scoring/assessment.py), or {prefiltered: true} for a job the title prefilter skipped.
export interface FitAssessment {
  skills_match?: Rating;
  experience_match?: Rating;
  domain_interest?: Rating;
  red_flags?: { code: string; note: string }[];
  matched_keywords?: string[];
  summary?: string;
  prefiltered?: boolean;
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
  hide_flagged?: boolean;
  source?: Source[];
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
  flagged?: number;
  source?: (FacetCount & { value: Source })[];
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
  packet?: string | null;
  // Local draft files served by the API (left out once the PDF has moved to Drive).
  resume_pdf?: string;
  resume_docx?: string;
  cover_letter_pdf?: string;
}

export interface Coverage {
  covered: string[];
  missing: string[];
  pct_required: number | null; // null when the posting lists no required qualifications
  pct_preferred: number | null;
}

export interface DraftHistoryEntry {
  version: number;
  at: string;
  rules_version: string;
}

/** POST /jobs/{key}/packet */
export interface PacketResult {
  prompt: string;
  doc_url: string | null;
  inline: boolean; // no Drive: the prompt carries the whole packet
  rules_version: string;
  drive_error?: string | null;
}

/** POST /jobs/{key}/result on success */
export interface ImportResult {
  outcome: { draft_version: number; warnings: string[]; coverage: Coverage; unmet: string[] };
  job: JobDetail;
}

export type PasteOutcome =
  | ({ ok: true } & ImportResult)
  | { ok: false; errors: string[]; warnings: string[]; fix_prompt: string };

export interface ClaudeDoc {
  kind: 'rules' | 'library';
  name: string;
  url: string | null;
  changed_at: string | null;
}

/** GET /claude/setup */
export interface ClaudeSetup {
  drive: boolean;
  drive_detail: string;
  /** "user": the user's own connected Drive; "legacy": the CLI's token file (owner, no TOKEN_KEY on the server). */
  drive_source: 'user' | 'legacy' | null;
  /** Where Connect Google Drive goes (a full-page redirect to Google), or null when the server can't offer it. */
  connect_url: string | null;
  rules_version: string;
  docs: ClaudeDoc[];
  project_name: string;
  project_instructions: string;
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
  source: Source;
  added_at: string | null;
  intake_status: IntakeStatus | null;
  intake_error: string | null;
  contacts: JobContact[];
  interviews: Interview[];
  fit_breakdown: FitComponent[];
  fit_flags: string[];
  flag_reasons: string[];
  assessment_stale: boolean;
  fit_assessment: FitAssessment | null;
  draft_version?: number | null;
  draft_rules_version?: string | null;
  submitted_draft_version?: number | null;
  submitted_rules_version?: string | null;
  coverage?: Coverage | null;
  unmet?: string[];
  warnings?: string[];
  notes_for_user?: string;
  draft_history?: DraftHistoryEntry[];
  packet_doc_url?: string | null;
}

export interface Criterion<V = unknown> {
  importance: Importance;
  value: V;
}

// The Preferences page's document (scoring/preferences.py Preferences).
export interface Preferences {
  salary: Criterion<{ min: number | null; currency: string; period: string }>;
  locations: Criterion<{ places: string[]; remote_ok: boolean }>;
  work_mode: Criterion<string[]>;
  role_category: Criterion<string[]>;
  seniority: Criterion<string[]>;
  employment_type: Criterion<string[]>;
  visa: Criterion<boolean>;
  clearance: Criterion<boolean>;
  travel: Criterion<number | null>;
  red_flags_counted: string[];
  skills_want: string[];
  skills_avoid: string[];
  industries_favor: string[];
  industries_avoid: string[];
  target_roles: string[];
}

export interface RedFlagOption {
  value: string;
  label: string;
  help: string;
}

export interface PreferencesResponse {
  prefs: Preferences;
  // Value lists by key; red_flags lists the pickable flags with a label and a one-line explanation.
  options: Record<string, string[] | RedFlagOption[]>;
  labels: Record<string, string>;
}

export interface ReassessPreview {
  reassess_needed: boolean;
  jobs_to_reassess: number;
  estimated_usd: number;
  /** Only with reassess_scope "active": jobs left out because they aren't extracted yet. */
  skipped_unextracted?: number;
}

/** "stale": only the jobs whose assessment is out of date. "active": every job that isn't rejected. */
export type ReassessScope = 'stale' | 'active';

/** GET /rescore/preview: what Rescore all would re-assess (active jobs) and roughly cost. */
export interface RescorePreview {
  jobs: number;
  skipped_unextracted: number;
  estimated_usd: number;
}

/** POST /rescore */
export interface RescoreAllResult extends RescorePreview {
  task: Task | null;
}

export interface SavePreferencesResult {
  prefs: Preferences;
  rescored: number;
  task: Task | null;
}

export type TaskKind = 'rescore' | 'reassess_all' | 'rescore_all' | 'intake';
export type TaskState = 'queued' | 'running' | 'done' | 'error';

export interface Task {
  id: string;
  url: string | null;
  kind: TaskKind;
  state: TaskState;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  error: string | null;
  key: string | null;
}

export type Relation = 'recruiter' | 'hiring_manager' | 'referral' | 'interviewer' | 'other';

// A contact (workspace.py), shared across jobs.
export interface Contact {
  id: string;
  name: string;
  company: string | null;
  role: string | null;
  email: string | null;
  phone: string | null;
  linkedin_url: string | null;
  notes: string | null;
  created_at: string;
  updated_at: string;
}

export type ContactFields = Partial<Omit<Contact, 'id' | 'created_at' | 'updated_at'>>;

export interface JobContact extends Contact {
  relation: Relation;
}

export interface ContactListItem extends Contact {
  job_count: number;
}

export interface ContactJob {
  key: string;
  title: string | null;
  company: string | null;
  relation: Relation;
}

export interface ContactWithJobs extends Contact {
  jobs: ContactJob[];
}

export type Outcome = 'pending' | 'passed' | 'rejected' | 'withdrew';

export interface Interview {
  id: string;
  job_url: string;
  scheduled_at: string | null; // ISO UTC
  stage: string | null;
  format: string | null;
  interviewers: string | null;
  prep_notes: string | null;
  debrief_notes: string | null;
  outcome: Outcome | null;
  created_at: string;
  updated_at: string;
}

export type InterviewFields = Partial<Pick<Interview,
  'scheduled_at' | 'stage' | 'format' | 'interviewers' | 'prep_notes' | 'debrief_notes' | 'outcome'>>;

export interface AddJobInput {
  url?: string;
  title?: string;
  company?: string;
  location?: string;
  description?: string;
}

export interface AddJobResult {
  key: string;
  created: boolean;
  task: Task | null;
}

export type PanelMode = 'latest_vs_previous' | 'rules_a_vs_b';
export type PanelGrader = 'sonnet' | 'opus' | 'you';
export type PanelWinner = 'first' | 'second' | 'tie';

export interface PanelRunSummary {
  id: string;
  created_at: string;
  mode: PanelMode;
  rules_version_a: string | null;
  rules_version_b: string | null;
  status: 'open' | 'complete';
}

/** GET /panel */
export interface PanelOverview {
  due: string | null;
  needed: number;
  comparable: Record<PanelMode, number>;
  runs: PanelRunSummary[];
  project_name: string;
  project_instructions: string;
  rubric: { name: string; url: string | null; text: string };
}

export interface PanelSideStats {
  mean_overall: number | null;
  mean_clarity: number | null;
  met_rate: { required: number | null; preferred: number | null };
  red_flags: number;
}

export interface PanelGraderStats {
  graded: number;
  newer_win_rate: number | null;
  a?: PanelSideStats;
  b?: PanelSideStats;
}

export interface PanelReport {
  items_total: number;
  items: {
    item: number;
    title: string | null;
    company: string;
    older_version: number | null;
    newer_version: number | null;
    newer_shown: 'first' | 'second';
    winners: Record<PanelGrader, 'newer' | 'older' | 'tie' | null>;
  }[];
  graders: Record<PanelGrader, PanelGraderStats>;
  agreement: { sonnet_vs_opus: number | null; sonnet_vs_you: number | null; opus_vs_you: number | null };
  verdict: 'newer is better' | 'no clear gain' | 'worse' | null;
  heard_back: { rules_version: string; submitted: number; heard_back: number; rate: number | null; note: string | null }[];
}

/** GET /panel/runs/{id}. The report is null until you've picked every item (it un-blinds them). */
export interface PanelRun {
  id: string;
  created_at: string;
  mode: PanelMode;
  status: 'open' | 'complete';
  packets: { packet: number; model: 'sonnet' | 'opus'; text: string; prompt: string }[];
  items: { item: number; title: string | null; company: string; first_text: string; second_text: string; your_pick: PanelWinner | null }[];
  graded: Record<PanelGrader, number>;
  report: PanelReport | null;
}

export type PanelGradeOutcome =
  | { ok: true; graded: number | null; run: PanelRun }
  | { ok: false; errors: string[] };

/** The signed-in account (GET /auth/me). While login is off every request acts as the owner. */
export interface AuthUser {
  email: string;
  name: string | null;
  role: 'owner' | 'user';
}

export interface AuthMe {
  /** Google sign-in is on (STRELITZIA_GOOGLE_CLIENT_ID is set on the server). */
  enabled: boolean;
  user: AuthUser | null;
}

/** A row on the owner's Users page (GET /admin/users). */
export interface AdminUser {
  id: string;
  email: string;
  name: string | null;
  role: 'owner' | 'user';
  status: 'invited' | 'active' | 'disabled';
  /** USD per calendar month; null = no cap. */
  monthly_cap_usd: number | null;
  /** Claude spend so far this month, including open batches at their estimate. */
  month_spend_usd: number;
  last_login_at: string | null;
}

/** The fields the Profile page edits (GET/PUT /profile). */
export interface ProfileFields {
  first_name: string;
  last_name: string;
  phone: string;
  email: string;
  linkedin_url: string;
  github_url: string;
  location: string;
  current_job_title: string;
  years_experience: number | null;
  education_level: string | null;
  skills: string[];
}

export interface ProfileResponse {
  profile: ProfileFields;
  /** Scoring has a resume text to read (uploaded, or imported from resume.txt). */
  has_resume: boolean;
  resume_file_name: string | null;
  content_library_md: string;
  /** Why the workbench can't use the content library, or null. */
  content_library_error: string | null;
  options: { education_level: string[] };
}

/** PUT /profile: the fields plus the content library. */
export type ProfileSave = ProfileFields & { content_library_md: string };

/** POST /profile/resume: the stored resume, and Claude's draft for the fields that were empty (not saved yet). */
export interface ResumeUploadResult extends ProfileResponse {
  draft: Partial<ProfileFields>;
  parse_error: string | null;
}
