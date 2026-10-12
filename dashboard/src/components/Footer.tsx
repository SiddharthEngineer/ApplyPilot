// AGPL-3.0 §13: users of the hosted dashboard get a link to the source.
export const SOURCE_URL = 'https://github.com/SiddharthEngineer/Strelitzia';
export const UPSTREAM_URL = 'https://github.com/Pickle-Pixel/ApplyPilot';

export default function Footer() {
  return (
    <footer className="footer">
      Strelitzia is free software (<a href="https://www.gnu.org/licenses/agpl-3.0.html">AGPL-3.0</a>) ·{' '}
      <a href={SOURCE_URL}>Source</a> · Based on <a href={UPSTREAM_URL}>ApplyPilot</a> by Pickle-Pixel
    </footer>
  );
}
