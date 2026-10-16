import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { useState } from 'react';
import { describe, expect, it, vi } from 'vitest';
import RolePicker from '../components/RolePicker';

const ROLES = {
  software_engineering: ['Software Engineer', 'Backend Engineer'],
  data_engineering: ['Data Engineer'],
  other: [],
};

/** Holds the value like the Preferences page does, and reports every change. */
function Harness({ fields, initial = [], spy }: { fields: string[]; initial?: string[]; spy: (v: string[]) => void }) {
  const [value, setValue] = useState(initial);
  return <RolePicker fields={fields} rolesByField={ROLES} value={value}
    onChange={(v) => { spy(v); setValue(v); }} />;
}

describe('RolePicker', () => {
  it('shows the suggested roles of each chosen field only', () => {
    render(<Harness fields={['software_engineering', 'other']} spy={vi.fn()} />);
    const group = screen.getByRole('group', { name: 'Software Engineering roles' });
    expect(within(group).getByRole('button', { name: 'Software Engineer' })).toHaveAttribute('aria-pressed', 'false');
    expect(screen.queryByRole('button', { name: 'Data Engineer' })).toBeNull();
    expect(screen.queryByRole('group', { name: 'Other roles' })).toBeNull(); // no suggestions, no group
  });

  it('ticks suggestions, adds custom roles on Enter and removes them', async () => {
    const spy = vi.fn();
    const user = userEvent.setup();
    render(<Harness fields={['software_engineering']} spy={spy} />);
    await user.click(screen.getByRole('button', { name: 'Software Engineer' }));
    await user.click(screen.getByRole('button', { name: 'Backend Engineer' }));
    expect(screen.getByRole('button', { name: 'Backend Engineer' })).toHaveAttribute('aria-pressed', 'true');
    await user.type(screen.getByLabelText('Add other role'), 'Compiler Engineer{Enter}');
    expect(spy).toHaveBeenLastCalledWith(['Software Engineer', 'Backend Engineer', 'Compiler Engineer']);
    expect(screen.getByLabelText('Add other role')).toHaveValue('');
    // Typing a suggestion (any case) ticks it rather than adding a duplicate.
    await user.click(screen.getByRole('button', { name: 'Software Engineer' }));
    await user.type(screen.getByLabelText('Add other role'), 'software engineer{Enter}');
    expect(spy).toHaveBeenLastCalledWith(['Backend Engineer', 'Compiler Engineer', 'Software Engineer']);
    await user.type(screen.getByLabelText('Add other role'), 'compiler engineer{Enter}');
    expect(spy).toHaveBeenCalledTimes(5); // already there: no change
    await user.click(screen.getByRole('button', { name: 'Remove Compiler Engineer' }));
    expect(spy).toHaveBeenLastCalledWith(['Backend Engineer', 'Software Engineer']);
  });

  it('matches titles case-insensitively, splits on commas and stores suggestions in their own case', async () => {
    const spy = vi.fn();
    const user = userEvent.setup();
    render(<Harness fields={['software_engineering']} initial={['software engineer']} spy={spy} />);
    expect(screen.getByRole('button', { name: 'Software Engineer' })).toHaveAttribute('aria-pressed', 'true');
    expect(screen.queryByRole('button', { name: 'Remove software engineer' })).toBeNull();
    // A suggestion of a field not chosen is stored as the suggestion; a comma separates titles.
    await user.type(screen.getByLabelText('Add other role'), 'data engineer, Compiler Engineer{Enter}');
    expect(spy).toHaveBeenLastCalledWith(['software engineer', 'Data Engineer', 'Compiler Engineer']);
    await user.click(screen.getByRole('button', { name: 'Software Engineer' }));
    expect(spy).toHaveBeenLastCalledWith(['Data Engineer', 'Compiler Engineer']);
  });

  it('keeps roles from a field no longer chosen as removable chips', async () => {
    const spy = vi.fn();
    const user = userEvent.setup();
    render(<Harness fields={['software_engineering']} initial={['Data Engineer', 'Software Engineer']} spy={spy} />);
    expect(screen.getByRole('button', { name: 'Software Engineer' })).toHaveAttribute('aria-pressed', 'true');
    await user.click(screen.getByRole('button', { name: 'Remove Data Engineer' }));
    expect(spy).toHaveBeenLastCalledWith(['Software Engineer']);
    expect(screen.queryByRole('button', { name: 'Remove Software Engineer' })).toBeNull();
  });
});
