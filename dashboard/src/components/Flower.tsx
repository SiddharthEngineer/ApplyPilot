// Original artwork, MIT, © 2026 Siddharth Engineer
// A bird-of-paradise (Strelitzia reginae) in profile: green spathe, flame-orange sepals, royal-blue arrow petals.
// Hand-written SVG on a 64×64 viewBox, no external assets. Colors come from the theme tokens and every animation
// is CSS in styles.css, inside @media (prefers-reduced-motion: no-preference).
import { useId, type CSSProperties } from 'react';

export type FlowerVariant = 'mark' | 'hero' | 'loader' | 'seedling';

interface FlowerProps {
  variant?: FlowerVariant;
  size?: number;
  /** Accessible name. Without one the flower is decorative (aria-hidden). */
  title?: string;
}

// Unfold/bloom order of each petal (styles.css staggers the animation by --i).
const step = (i: number) => ({ '--i': i }) as CSSProperties;

function Bloom() {
  return (
    <g className="sway">
      <path className="leaf" d="M31 62C24 58 14 52 7 41c10 4 19 10 25 16z" />
      <path className="leaf-vein" d="M30.5 60.5C23 55 15 49 8.5 42.5" />
      <path className="stem" d="M32 63c0-9-2-17-5-24" />
      <g className="bloom">
        <path className="sepal" style={step(0)} d="M25 36c-4-9-7-19-8-29 5 8 9 18 11.5 28z" />
        <path className="sepal" style={step(1)} d="M26.5 35.5c-.5-10.5.5-20.5 3.5-30.5 1.5 10 .5 20-.5 30z" />
        <path className="sepal" style={step(2)} d="M28 35.5c4-8.5 9-17.5 16-25.5-3 9-8 18-13 26z" />
        <path className="petal" style={step(3)} d="M29 35c4-5 9-10 14-14l-1.5-1.5L47 18l-1.5 5.5L44 22c-5 4.5-10 9-13 14z" />
        <path className="petal" style={step(4)} d="M27.5 35c0-5 1-10 3-15l-1.5-.2 3-4.3 1 5-1.4-.2c-1.4 4.7-2 9.7-2 15.2z" />
      </g>
      <path className="spathe" d="M9 40.5c9 6 30 4 48-8.5-13 3-29 4-40 3.5-4 0-7 2-8 5z" />
      <path className="spathe-edge" d="M17 35.6c13 .5 27-.4 39-3.4" />
    </g>
  );
}

function Seedling() {
  return (
    <>
      <ellipse className="soil" cx="32" cy="61" rx="13" ry="2" />
      <g className="sway">
        <path className="stem" d="M32 61c0-8-1-14 0-21" />
        <path className="leaf" d="M32 47c-6 0-11-4-13-10 6 0 11 4 13 10z" />
        <path className="leaf" d="M32 43c5-1 10-5 12-11-6 0-11 4-12 11z" />
        <path className="sepal" d="M32 40c-2-4-1-8 2-10 0 4 0 7-2 10z" />
      </g>
    </>
  );
}

export default function Flower({ variant = 'mark', size = 24, title }: FlowerProps) {
  const glow = `sun-${useId().replace(/:/g, '')}`;
  const a11y = title ? { role: 'img', 'aria-label': title } : { 'aria-hidden': true };
  return (
    <svg
      className={`flower flower-${variant}`}
      data-variant={variant}
      viewBox="0 0 64 64"
      width={size}
      height={size}
      focusable="false"
      {...a11y}
    >
      {variant === 'hero' && (
        <>
          <defs>
            <radialGradient id={glow}>
              <stop offset="0" style={{ stopColor: 'var(--gold)', stopOpacity: 1 }} />
              <stop offset="1" style={{ stopColor: 'var(--gold)', stopOpacity: 0 }} />
            </radialGradient>
          </defs>
          <circle className="sun" cx="30" cy="22" r="22" style={{ fill: `url(#${glow})` }} />
        </>
      )}
      {variant === 'seedling' ? <Seedling /> : <Bloom />}
    </svg>
  );
}
