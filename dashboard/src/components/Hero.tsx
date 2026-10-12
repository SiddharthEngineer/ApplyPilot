import type { Facets, Status } from '../types';
import Flower from './Flower';

export const TAGLINE = 'Bring your search into the light.';

// Pipeline counts from the facets endpoint (no extra request).
const STATS: { label: string; status?: Status }[] = [
  { label: 'Jobs found' },
  { label: 'Active', status: 'active' },
  { label: 'In progress', status: 'in_progress' },
  { label: 'Submitted', status: 'submitted' },
  { label: 'Heard back', status: 'heard_back' },
];

export default function Hero({ facets }: { facets: Facets | null }) {
  const count = (status?: Status) =>
    status ? facets?.status.find((s) => s.value === status)?.count ?? 0 : facets?.total ?? 0;
  return (
    <section className="hero" aria-label="Overview">
      <Flower variant="hero" size={112} title="Strelitzia flower" />
      <div className="hero-text">
        <p className="hero-title">Strelitzia</p>
        <p className="tagline">{TAGLINE}</p>
        {facets && (
          <ul className="hero-stats">
            {STATS.map(({ label, status }) => (
              <li key={label}>
                <strong>{count(status).toLocaleString()}</strong>
                <span>{label}</span>
              </li>
            ))}
          </ul>
        )}
      </div>
    </section>
  );
}
