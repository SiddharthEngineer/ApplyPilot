import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import * as api from '../api';
import { ApiError } from '../api';
import ProfilePage from '../pages/ProfilePage';
import type { ProfileFields, ProfileResponse, ResumeUploadResult } from '../types';

vi.mock('../api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api')>();
  return { ...actual, getProfile: vi.fn(), saveProfile: vi.fn(), uploadResume: vi.fn() };
});

// Synthetic profile, no real person.
const FIELDS: ProfileFields = {
  first_name: 'Ada', last_name: '', phone: '555-000-0000', email: '', linkedin_url: '', github_url: '',
  location: '', current_job_title: '', years_experience: null, education_level: null, skills: [],
};
const RESPONSE: ProfileResponse = {
  profile: FIELDS, has_resume: false, resume_file_name: null, content_library_md: '',
  content_library_error: null, options: { education_level: ['high_school', 'bachelors', 'masters', 'phd'] },
};

function renderPage() {
  render(<MemoryRouter><ProfilePage /></MemoryRouter>);
  return userEvent.setup();
}

const pdf = () => new File(['%PDF-1.4'], 'cv.pdf', { type: 'application/pdf' });

beforeEach(() => {
  vi.mocked(api.getProfile).mockResolvedValue(structuredClone(RESPONSE));
  vi.mocked(api.saveProfile).mockImplementation(async (p) => {
    const { content_library_md, ...profile } = p;
    return { ...RESPONSE, profile, content_library_md };
  });
});

describe('ProfilePage', () => {
  it('shows the saved profile and the header line', async () => {
    renderPage();
    expect(await screen.findByLabelText('First name')).toHaveValue('Ada');
    expect(screen.getByLabelText('Phone')).toHaveValue('555-000-0000');
    expect(screen.getByText(/Profile is about you;/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Save' })).toBeDisabled();
  });

  it('saves edited fields and the content library', async () => {
    const user = renderPage();
    const phone = await screen.findByLabelText('Phone');
    await user.clear(phone);
    await user.type(phone, '555-111-2222');
    await user.type(screen.getByLabelText('Skills'), 'Python{Enter}');
    await user.type(screen.getByLabelText('Content library'), '## CURRENT ROLE: Widgets');
    await user.click(screen.getByRole('button', { name: 'Save' }));
    expect(api.saveProfile).toHaveBeenCalledWith({
      ...FIELDS, phone: '555-111-2222', skills: ['Python'], content_library_md: '## CURRENT ROLE: Widgets',
    });
    expect(await screen.findByText('Saved.')).toBeInTheDocument();
  });

  it('shows the draft from the resume, highlighted, without saving it until Save', async () => {
    let finish: (r: ResumeUploadResult) => void = () => {};
    vi.mocked(api.uploadResume).mockReturnValue(new Promise((resolve) => { finish = resolve; }));
    const user = renderPage();
    await screen.findByLabelText('First name');
    await user.upload(screen.getByLabelText('Upload resume'), pdf());
    expect(screen.getByText('Parsing…')).toBeInTheDocument();
    // The server only drafts empty fields; a field typed meanwhile would win too.
    finish({
      ...RESPONSE, has_resume: true, resume_file_name: 'cv.pdf', parse_error: null,
      draft: { last_name: 'Example', current_job_title: 'Widget Engineer', education_level: 'masters',
        years_experience: 4, skills: ['Python', 'SQL'] },
    });
    expect(await screen.findByLabelText('Last name')).toHaveValue('Example');
    expect(screen.getByLabelText('Current title')).toHaveValue('Widget Engineer');
    expect(screen.getByLabelText('Education')).toHaveValue('masters');
    expect(screen.getByLabelText('Years of experience')).toHaveValue(4);
    expect(screen.getByText('SQL')).toBeInTheDocument();
    expect(screen.getByLabelText('First name')).toHaveValue('Ada');
    expect(screen.getAllByText('From resume')).toHaveLength(5);
    expect(screen.getByLabelText('Last name').closest('.pref-row')).toHaveClass('drafted');
    expect(screen.getByRole('link', { name: 'cv.pdf' })).toBeInTheDocument();
    expect(screen.getByText(/The highlighted fields are from it/)).toBeInTheDocument();
    expect(api.saveProfile).not.toHaveBeenCalled();

    await user.click(screen.getByRole('button', { name: 'Save' }));
    expect(api.saveProfile).toHaveBeenCalledWith(expect.objectContaining({
      first_name: 'Ada', last_name: 'Example', current_job_title: 'Widget Engineer', education_level: 'masters',
      years_experience: 4, skills: ['Python', 'SQL'],
    }));
    await waitFor(() => expect(screen.queryAllByText('From resume')).toHaveLength(0));
  });

  it('a field typed while parsing keeps the typed value and no highlight', async () => {
    let finish: (r: ResumeUploadResult) => void = () => {};
    vi.mocked(api.uploadResume).mockReturnValue(new Promise((resolve) => { finish = resolve; }));
    const user = renderPage();
    await screen.findByLabelText('First name');
    await user.upload(screen.getByLabelText('Upload resume'), pdf());
    await user.type(screen.getByLabelText('Last name'), 'Typed');
    finish({ ...RESPONSE, has_resume: true, resume_file_name: 'cv.pdf', parse_error: null,
      draft: { last_name: 'Example', location: 'Springfield' } });
    expect(await screen.findByLabelText('Location')).toHaveValue('Springfield');
    await waitFor(() => expect(screen.getAllByText('From resume')).toHaveLength(1));
    expect(screen.getByLabelText('Last name')).toHaveValue('Typed');
    expect(screen.getByLabelText('Last name').closest('.pref-row')).not.toHaveClass('drafted');
  });

  it('discarding a draft puts the saved values back', async () => {
    vi.mocked(api.uploadResume).mockResolvedValue({
      ...RESPONSE, has_resume: true, resume_file_name: 'cv.pdf', parse_error: null, draft: { last_name: 'Example' },
    });
    const user = renderPage();
    await screen.findByLabelText('First name');
    await user.upload(screen.getByLabelText('Upload resume'), pdf());
    expect(await screen.findByLabelText('Last name')).toHaveValue('Example');
    await user.click(screen.getByRole('button', { name: 'Discard' }));
    expect(screen.getByLabelText('Last name')).toHaveValue('');
    expect(screen.queryByText('From resume')).not.toBeInTheDocument();
  });

  it('shows the parse error and the upload error', async () => {
    vi.mocked(api.uploadResume).mockResolvedValueOnce({
      ...RESPONSE, has_resume: true, resume_file_name: 'cv.pdf', draft: {},
      parse_error: "The resume was saved, but it couldn't be parsed: ANTHROPIC_API_KEY is not set",
    });
    const user = renderPage();
    await screen.findByLabelText('First name');
    await user.upload(screen.getByLabelText('Upload resume'), pdf());
    expect(await screen.findByText(/ANTHROPIC_API_KEY is not set/)).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'cv.pdf' })).toBeInTheDocument();

    vi.mocked(api.uploadResume).mockRejectedValueOnce(new ApiError(422, 'Upload a PDF or DOCX resume'));
    await user.upload(screen.getByLabelText('Upload resume'), pdf());
    expect(await screen.findByText('Upload a PDF or DOCX resume')).toBeInTheDocument();
  });

  it('shows the content library parse error from the API', async () => {
    vi.mocked(api.getProfile).mockResolvedValue({
      ...RESPONSE, content_library_md: 'notes', content_library_error: 'No roles found: each role starts with …',
    });
    renderPage();
    expect(await screen.findByText(/No roles found/)).toBeInTheDocument();
    expect(screen.getByLabelText('Content library')).toHaveValue('notes');
  });
});
