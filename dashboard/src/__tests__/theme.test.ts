import { describe, expect, it } from 'vitest';
import css from '../styles.css?raw';

/** The `{...}` body that starts at the first `{` after `from`, with nested braces. */
function block(source: string, from: number): { body: string; end: number } {
  const open = source.indexOf('{', from);
  let depth = 0;
  for (let i = open; i < source.length; i++) {
    if (source[i] === '{') depth++;
    if (source[i] === '}' && --depth === 0) return { body: source.slice(open + 1, i), end: i + 1 };
  }
  throw new Error('unbalanced braces');
}

function tokens(body: string): Record<string, string> {
  const out: Record<string, string> = {};
  for (const m of body.matchAll(/--([\w-]+):\s*([^;]+);/g)) out[m[1]] = m[2].trim();
  return out;
}

const light = tokens(block(css, css.indexOf(':root')).body);
const darkMedia = block(css, css.indexOf('@media (prefers-color-scheme: dark)')).body;
const dark = { ...light, ...tokens(block(darkMedia, darkMedia.indexOf(':root')).body) };

function resolve(theme: Record<string, string>, name: string): string {
  const value = theme[name];
  const ref = value?.match(/^var\(--([\w-]+)\)$/);
  return ref ? resolve(theme, ref[1]) : value;
}

function luminance(hex: string): number {
  const [r, g, b] = [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16) / 255)
    .map((c) => (c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4));
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

function contrast(a: string, b: string): number {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x);
  return (hi + 0.05) / (lo + 0.05);
}

// [foreground, background] token pairs used for text and buttons.
const PAIRS: [string, string][] = [
  ...['bg', 'surface', 'surface-2'].flatMap((bg) => [['text', bg], ['muted', bg], ['link', bg]] as [string, string][]),
  ['danger', 'surface'],
  ['danger', 'bg'],
  ['accent-text', 'accent'], // primary buttons and pressed filters
  ...['grey', 'orange', 'yellow', 'blue', 'red', 'green'].map((c) => [`badge-${c}-fg`, `badge-${c}-bg`] as [string, string]),
  ['badge-green-fg', 'surface'], // .tag-remote
  ['badge-blue-fg', 'surface'], // .tag-hybrid
];

describe('Sunrise theme tokens', () => {
  it('uses the planned palette', () => {
    expect(light).toMatchObject({
      bg: '#fbf6ee', surface: '#ffffff', 'surface-2': '#f5ede0', accent: '#e8711a',
      secondary: '#3a3fa8', success: '#2f7a4f', gold: '#f2b33d',
    });
    expect(dark).toMatchObject({
      bg: '#14122b', surface: '#1d1a3a', 'surface-2': '#27234a', accent: '#ff9a3c',
      secondary: '#8f95ff', success: '#6fd49a',
    });
  });

  for (const [name, theme] of [['light', light], ['dark', dark]] as const) {
    it.each(PAIRS)(`${name}: --%s on --%s is at least 4.5:1`, (fg, bg) => {
      expect(contrast(resolve(theme, fg), resolve(theme, bg))).toBeGreaterThanOrEqual(4.5);
    });
  }
});

describe('reduced motion', () => {
  it('keeps every animation and transition inside prefers-reduced-motion: no-preference', () => {
    const guard = '@media (prefers-reduced-motion: no-preference)';
    const start = css.indexOf(guard);
    expect(start).toBeGreaterThan(-1);
    const { body, end } = block(css, start);
    const outside = css.slice(0, start) + css.slice(end);
    expect(outside).not.toMatch(/(^|[\s;{])(animation|animation-name|transition)\s*:/);
    expect(body).toMatch(/\.flower-hero .*animation:/);
    expect(body).toMatch(/\.flower-loader .*animation:/);
  });
});
