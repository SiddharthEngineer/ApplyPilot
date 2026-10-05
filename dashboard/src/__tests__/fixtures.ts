import type { Facets, JobDetail, JobList, JobListItem, Status, StatusColor } from '../types';

export const COLORS: Record<Status, StatusColor> = {
  active: 'grey', inactive: 'orange', in_progress: 'yellow', submitted: 'blue', rejected: 'red', heard_back: 'green',
};

export function listItem(status: Status, n: number, extra: Partial<JobListItem> = {}): JobListItem {
  return {
    key: `key${n}`,
    title: `Job ${n}`,
    company: `Company ${n}`,
    role_category: 'data_science',
    location: 'Chicago, IL',
    work_mode: 'hybrid',
    fit_score: 7,
    discovered_at: '2026-10-02T06:59:45+00:00',
    deadline: null,
    status,
    status_color: COLORS[status],
    days_since_submitted: status === 'submitted' ? 3 : null,
    ...extra,
  };
}

export function jobList(items: JobListItem[], total = items.length): JobList {
  return { total, page: 1, page_size: 50, items };
}

export const FACETS: Facets = {
  total: 10,
  status: [
    { value: 'active', count: 5, color: 'grey' },
    { value: 'inactive', count: 1, color: 'orange' },
    { value: 'in_progress', count: 1, color: 'yellow' },
    { value: 'submitted', count: 2, color: 'blue' },
    { value: 'rejected', count: 1, color: 'red' },
    { value: 'heard_back', count: 0, color: 'green' },
    { value: 'no_response', count: 1, color: null },
  ],
  role_category: [{ value: 'data_science', count: 6 }, { value: 'software_engineering', count: 4 }],
  work_mode: [{ value: 'remote', count: 4 }, { value: 'hybrid', count: 3 }],
  location: [{ value: 'Chicago, IL', count: 4 }, { value: 'Remote, US', count: 3 }],
};

export function jobDetail(extra: Partial<JobDetail> = {}): JobDetail {
  return {
    key: 'abc123',
    url: 'https://example.com/job/1',
    title: 'Data Scientist',
    company: 'Acme',
    site: 'Acme',
    location: 'Chicago, IL',
    work_mode: 'hybrid',
    role_category: 'data_science',
    fit_score: 8,
    score_reasoning: 'Strong match on Python and SQL.',
    discovered_at: '2026-10-02T06:59:45+00:00',
    deadline: '2026-11-01',
    notes: null,
    description_text: 'We are hiring a data scientist to build models.',
    details: {
      company: 'Acme', title_normalized: 'Data Scientist', role_category: 'data_science', seniority: 'mid',
      employment_type: 'full_time', work_mode: 'hybrid', locations: [{ city: 'Chicago', state: 'IL', country: 'US' }],
      remote_region: null, salary_min: 120000, salary_max: 150000, salary_currency: 'USD', salary_period: 'year',
      posted_date: '2026-09-30', application_deadline: '2026-11-01', start_date: null,
      summary: 'Build forecasting models for the supply chain.', team: 'Forecasting',
      responsibilities: ['Own demand forecasts'], required_qualifications: ['3+ years of Python'],
      preferred_qualifications: ['PhD in statistics'], skills_required: ['Python', 'SQL'], skills_preferred: ['Spark'],
      education_level: 'masters', education_fields: ['Statistics'], years_experience_min: 3, years_experience_max: null,
      visa_sponsorship: 'no', security_clearance: 'none', travel: null, benefits: ['401(k) match'], company_blurb: null,
    },
    status: 'active',
    status_color: 'grey',
    days_since_submitted: null,
    submitted_date: null,
    events: [],
    links: {
      posting: 'https://example.com/job/1', apply: 'https://example.com/job/1/apply',
      drive_folder: null, resume: null, cover_letter: null,
    },
    ...extra,
  };
}
