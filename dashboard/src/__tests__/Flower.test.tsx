import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import Flower, { type FlowerVariant } from '../components/Flower';
import Footer, { SOURCE_URL } from '../components/Footer';
import Hero, { TAGLINE } from '../components/Hero';
import { FACETS } from './fixtures';

const VARIANTS: FlowerVariant[] = ['mark', 'hero', 'loader', 'seedling'];

describe('Flower', () => {
  it.each(VARIANTS)('renders the %s variant on a 64×64 viewBox', (variant) => {
    const { container } = render(<Flower variant={variant} size={40} />);
    const svg = container.querySelector('svg')!;
    expect(svg).toHaveAttribute('viewBox', '0 0 64 64');
    expect(svg).toHaveAttribute('width', '40');
    expect(svg).toHaveClass('flower', `flower-${variant}`);
    expect(svg).toHaveAttribute('aria-hidden', 'true');
    // No external assets: nothing loads from a URL.
    expect(container.querySelector('image, use[href], [href^="http"]')).toBeNull();
  });

  it('draws the flower (sepals, blue petals, spathe) except for the seedling', () => {
    const flower = render(<Flower variant="mark" />).container;
    expect(flower.querySelectorAll('.sepal')).toHaveLength(3);
    expect(flower.querySelectorAll('.petal')).toHaveLength(2);
    expect(flower.querySelector('.spathe')).not.toBeNull();
    const seedling = render(<Flower variant="seedling" />).container;
    expect(seedling.querySelector('.spathe')).toBeNull();
    expect(seedling.querySelector('.soil')).not.toBeNull();
  });

  it('gives only the hero a sun glow, with an id unique per instance', () => {
    const { container } = render(<><Flower variant="hero" /><Flower variant="hero" /><Flower variant="mark" /></>);
    const ids = [...container.querySelectorAll('radialGradient')].map((g) => g.id);
    expect(ids).toHaveLength(2);
    expect(new Set(ids).size).toBe(2);
    expect(container.querySelectorAll('.sun')).toHaveLength(2);
  });

  it('is an image with a name when given a title', () => {
    render(<Flower variant="hero" title="Strelitzia flower" />);
    expect(screen.getByRole('img', { name: 'Strelitzia flower' })).toBeInTheDocument();
  });
});

describe('Hero', () => {
  it('shows the tagline and pipeline counts from the facets', () => {
    render(<Hero facets={FACETS} />);
    expect(screen.getByText(TAGLINE)).toHaveTextContent('Bring your search into the light.');
    const stat = (label: string) => screen.getByText(label).previousElementSibling;
    expect(stat('Jobs found')).toHaveTextContent('10');
    expect(stat('Active')).toHaveTextContent('5');
    expect(stat('Submitted')).toHaveTextContent('2');
    expect(stat('Heard back')).toHaveTextContent('0');
  });
});

describe('Footer', () => {
  it('links to the source (AGPL §13) and credits the original project', () => {
    render(<Footer />);
    expect(screen.getByRole('link', { name: 'Source' })).toHaveAttribute('href', SOURCE_URL);
    expect(SOURCE_URL).toBe('https://github.com/SiddharthEngineer/Strelitzia');
    expect(screen.getByRole('contentinfo')).toHaveTextContent(
      'Strelitzia is free software (AGPL-3.0) · Source · Based on ApplyPilot by Pickle-Pixel',
    );
  });
});
