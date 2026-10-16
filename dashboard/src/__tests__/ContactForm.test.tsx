import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { afterEach, describe, expect, it, vi } from 'vitest';
import * as api from '../api';
import ContactForm, { contactProblem } from '../components/ContactForm';
import ContactsPage from '../pages/ContactsPage';
import { contact } from './fixtures';

vi.mock('../api');

afterEach(() => vi.restoreAllMocks());

describe('ContactForm', () => {
  it('has a labelled input for every field, prefilled from initial', () => {
    render(<ContactForm initial={{ name: 'Ana', company: 'Acme' }} onSubmit={vi.fn()} onCancel={vi.fn()} />);
    expect(screen.getByLabelText('Name')).toHaveValue('Ana');
    expect(screen.getByLabelText('Name')).toBeRequired();
    expect(screen.getByLabelText('Email')).toHaveAttribute('type', 'email');
    expect(screen.getByLabelText('Phone')).toHaveAttribute('type', 'tel');
    expect(screen.getByLabelText('LinkedIn')).toHaveAttribute('type', 'url');
    expect(screen.getByLabelText('Company')).toHaveValue('Acme');
    expect(screen.getByLabelText('Role')).toHaveValue('');
    expect(screen.getByLabelText('Notes')).toHaveValue('');
  });

  it('submits trimmed fields and leaves empty ones out', async () => {
    const user = userEvent.setup();
    const onSubmit = vi.fn().mockResolvedValue(undefined);
    render(<ContactForm onSubmit={onSubmit} onCancel={vi.fn()} />);
    await user.type(screen.getByLabelText('Name'), ' Ana Ruiz ');
    await user.type(screen.getByLabelText('Email'), 'ana@acme.com');
    await user.type(screen.getByLabelText('Phone'), '+1 555 0100 ');
    await user.type(screen.getByLabelText('LinkedIn'), 'https://www.linkedin.com/in/ana');
    await user.click(screen.getByRole('button', { name: 'Save contact' }));
    expect(onSubmit).toHaveBeenCalledWith({
      name: 'Ana Ruiz', email: 'ana@acme.com', phone: '+1 555 0100', linkedin_url: 'https://www.linkedin.com/in/ana',
    });
  });

  it('checks the name, email and LinkedIn link before submitting', async () => {
    const user = userEvent.setup();
    const onSubmit = vi.fn();
    render(<ContactForm onSubmit={onSubmit} onCancel={vi.fn()} />);
    await user.click(screen.getByRole('button', { name: 'Save contact' }));
    expect(screen.getByRole('alert')).toHaveTextContent('Name is required.');
    await user.type(screen.getByLabelText('Name'), 'Ana');
    await user.type(screen.getByLabelText('Email'), 'ana.acme.com');
    await user.click(screen.getByRole('button', { name: 'Save contact' }));
    expect(screen.getByRole('alert')).toHaveTextContent('Email must contain @.');
    await user.clear(screen.getByLabelText('Email'));
    await user.type(screen.getByLabelText('LinkedIn'), 'https://example.com/in/ana');
    await user.click(screen.getByRole('button', { name: 'Save contact' }));
    expect(screen.getByRole('alert')).toHaveTextContent('LinkedIn must be an http(s) link on linkedin.com.');
    expect(onSubmit).not.toHaveBeenCalled();
  });

  it('shows the API error inline', async () => {
    const user = userEvent.setup();
    const onSubmit = vi.fn().mockRejectedValue(new Error('Email must contain @'));
    render(<ContactForm initial={{ name: 'Ana' }} onSubmit={onSubmit} onCancel={vi.fn()} />);
    await user.click(screen.getByRole('button', { name: 'Save contact' }));
    expect(await screen.findByRole('alert')).toHaveTextContent(/^Email must contain @$/);
    expect(screen.getByRole('button', { name: 'Save contact' })).toBeEnabled();
  });

  it('cancels', async () => {
    const user = userEvent.setup();
    const onCancel = vi.fn();
    render(<ContactForm onSubmit={vi.fn()} onCancel={onCancel} />);
    await user.click(screen.getByRole('button', { name: 'Cancel' }));
    expect(onCancel).toHaveBeenCalled();
  });

  it('matches the API checks', () => {
    expect(contactProblem({ name: 'A', linkedin_url: 'https://linkedin.com/in/a' })).toBeNull();
    expect(contactProblem({ name: 'A', linkedin_url: 'http://uk.linkedin.com/in/a' })).toBeNull();
    expect(contactProblem({ name: 'A', linkedin_url: 'ftp://linkedin.com/in/a' })).not.toBeNull();
    expect(contactProblem({ name: 'A', linkedin_url: 'https://notlinkedin.com/in/a' })).not.toBeNull();
    expect(contactProblem({ name: 'A', linkedin_url: 'linkedin.com/in/a' })).not.toBeNull();
    expect(contactProblem({ name: 'A', linkedin_url: 'https:linkedin.com/in/a' })).not.toBeNull();
    expect(contactProblem({ name: 'A', linkedin_url: 'https://evil.com\\@www.linkedin.com/in/a' })).not.toBeNull();
  });
});

describe('Contacts page: New contact', () => {
  it('creates a contact and opens it in the editor', async () => {
    const user = userEvent.setup();
    const ana = contact({ id: 'new1', phone: '+1 555 0100' });
    vi.mocked(api.listContacts).mockResolvedValue([]);
    vi.mocked(api.createContact).mockResolvedValue(ana);
    vi.mocked(api.getContact).mockResolvedValue({ ...ana, jobs: [] });
    render(<MemoryRouter initialEntries={['/contacts']}>
      <Routes><Route path="/contacts" element={<ContactsPage />} /></Routes>
    </MemoryRouter>);
    expect(await screen.findByText('No contacts yet. Add one with New contact.')).toBeInTheDocument();

    vi.mocked(api.listContacts).mockResolvedValue([{ ...ana, job_count: 0 }]);
    await user.click(screen.getByRole('button', { name: 'New contact' }));
    const form = screen.getByRole('form', { name: 'New contact' });
    await user.type(screen.getByLabelText('Name'), 'Ana Ruiz');
    await user.type(screen.getByLabelText('Phone'), '+1 555 0100');
    await user.click(screen.getByRole('button', { name: 'Create contact' }));
    expect(api.createContact).toHaveBeenCalledWith({ name: 'Ana Ruiz', phone: '+1 555 0100' });
    await waitFor(() => expect(form).not.toBeInTheDocument());
    expect(await screen.findByRole('region', { name: 'Edit Ana Ruiz' })).toBeInTheDocument();
    expect(api.getContact).toHaveBeenCalledWith('new1', expect.anything());
    expect(await screen.findByRole('button', { name: 'Ana Ruiz' })).toBeInTheDocument();
  });
});
