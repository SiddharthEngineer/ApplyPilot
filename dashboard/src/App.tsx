import { useState } from 'react';
import { Link, NavLink, Route, Routes } from 'react-router-dom';
import AddJobDialog from './components/AddJobDialog';
import Flower from './components/Flower';
import Footer from './components/Footer';
import ContactsPage from './pages/ContactsPage';
import JobPage from './pages/JobPage';
import JobsPage from './pages/JobsPage';
import PreferencesPage from './pages/PreferencesPage';

export default function App() {
  const [adding, setAdding] = useState(false);
  return (
    <div className="app">
      <header className="topbar">
        <Link to="/" className="brand">
          <Flower variant="mark" size={28} />
          Strelitzia
        </Link>
        <nav className="nav" aria-label="Main">
          <NavLink to="/" end>Jobs</NavLink>
          <NavLink to="/contacts">Contacts</NavLink>
          <NavLink to="/preferences">Preferences</NavLink>
        </nav>
        <button type="button" className="primary add-job-button" onClick={() => setAdding(true)}>Add job</button>
      </header>
      {adding && <AddJobDialog onClose={() => setAdding(false)} />}
      <main className="content">
        <Routes>
          <Route path="/" element={<JobsPage />} />
          <Route path="/jobs/:key" element={<JobPage />} />
          <Route path="/contacts" element={<ContactsPage />} />
          <Route path="/preferences" element={<PreferencesPage />} />
          <Route path="*" element={<p className="notice">Page not found. <Link to="/">Back to jobs</Link></p>} />
        </Routes>
      </main>
      <Footer />
    </div>
  );
}
