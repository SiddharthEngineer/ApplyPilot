import { Link, Route, Routes } from 'react-router-dom';
import JobPage from './pages/JobPage';
import JobsPage from './pages/JobsPage';

export default function App() {
  return (
    <div className="app">
      <header className="topbar">
        <Link to="/" className="brand">ApplyPilot</Link>
      </header>
      <main className="content">
        <Routes>
          <Route path="/" element={<JobsPage />} />
          <Route path="/jobs/:key" element={<JobPage />} />
          <Route path="*" element={<p className="notice">Page not found. <Link to="/">Back to jobs</Link></p>} />
        </Routes>
      </main>
    </div>
  );
}
