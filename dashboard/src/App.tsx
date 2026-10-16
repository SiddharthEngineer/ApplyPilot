import { useCallback, useEffect, useState } from 'react';
import { getMe, getPanel, logout, onUnauthorized } from './api';
import { Link, NavLink, Route, Routes } from 'react-router-dom';
import AddJobDialog from './components/AddJobDialog';
import Flower from './components/Flower';
import Footer from './components/Footer';
import ClaudeSetupPage from './pages/ClaudeSetupPage';
import ContactsPage from './pages/ContactsPage';
import JobPage from './pages/JobPage';
import JobsPage from './pages/JobsPage';
import PanelPage from './pages/PanelPage';
import PreferencesPage from './pages/PreferencesPage';
import SignInPage from './pages/SignInPage';
import type { AuthMe } from './types';

/** The sign-in gate: with login on, no session (or any 401) shows the sign-in screen instead of the app. */
export default function App() {
  const [me, setMe] = useState<AuthMe | null>(null);
  const [signedOut, setSignedOut] = useState(false);
  useEffect(() => {
    onUnauthorized(() => setSignedOut(true));
    getMe().then(setMe).catch(() => setMe({ enabled: false, user: null })); // an older server: no login
    return () => onUnauthorized(null);
  }, []);
  // The app renders while /auth/me loads (login off is the common case); a 401 meanwhile lands on sign-in too.
  if (signedOut || (me?.enabled && !me.user)) return <SignInPage />;
  return <Shell me={me} />;
}

function Shell({ me }: { me: AuthMe | null }) {
  const [adding, setAdding] = useState(false);
  const [panelDue, setPanelDue] = useState<string | null>(null);
  const refreshPanelDue = useCallback(() => {
    getPanel().then((p) => setPanelDue(p.due)).catch(() => {}); // the reminder is optional
  }, []);
  useEffect(refreshPanelDue, [refreshPanelDue]);
  function signOut() {
    logout().catch(() => {}).finally(() => window.location.reload());
  }
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
          <NavLink to="/panel">
            Panel{panelDue && <span className="nav-dot" role="img" aria-label="due" title={`Panel due: ${panelDue}`} />}
          </NavLink>
          <NavLink to="/claude-setup">Claude setup</NavLink>
        </nav>
        <button type="button" className="primary add-job-button" onClick={() => setAdding(true)}>Add job</button>
        {me?.enabled && me.user && (
          <span className="user-menu">
            <span className="user-name" title={me.user.email}>{me.user.name || me.user.email}</span>
            <button type="button" onClick={signOut}>Sign out</button>
          </span>
        )}
      </header>
      {adding && <AddJobDialog onClose={() => setAdding(false)} />}
      <main className="content">
        <Routes>
          <Route path="/" element={<JobsPage panelDue={panelDue} />} />
          <Route path="/jobs/:key" element={<JobPage />} />
          <Route path="/contacts" element={<ContactsPage />} />
          <Route path="/preferences" element={<PreferencesPage />} />
          <Route path="/claude-setup" element={<ClaudeSetupPage />} />
          <Route path="/panel" element={<PanelPage onChange={refreshPanelDue} />} />
          <Route path="*" element={<p className="notice">Page not found. <Link to="/">Back to jobs</Link></p>} />
        </Routes>
      </main>
      <Footer />
    </div>
  );
}
