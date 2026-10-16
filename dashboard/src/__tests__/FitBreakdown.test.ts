import { describe, expect, it } from 'vitest';
import { componentScore } from '../components/FitBreakdown';
import type { FitComponent } from '../types';

const flags = (score: number, note: string): FitComponent => ({
  key: 'red_flags', label: 'Red flags', score, importance: 'important', dealbreaker_hit: false, note,
});

describe('componentScore', () => {
  it('red flags: none, only ones the user does not count, or a counted one', () => {
    expect(componentScore(flags(1, 'none'))).toBe('none');
    expect(componentScore(flags(1, 'relocation required (not counted)'))).toBe('✓');
    expect(componentScore(flags(0, 'vague posting'))).toBe('⚠');
  });
});
