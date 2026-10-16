import { useSearchParams } from 'react-router-dom';
import { LOGIN_URL } from '../api';
import Flower from '../components/Flower';

// The ?auth= outcome the sign-in callback (web/auth.py) redirects back with.
const AUTH_OUTCOMES: Record<string, string> = {
  not_invited: "This Google account isn't invited. Ask the owner to invite your email, then sign in again.",
  disabled: "This account has been disabled. Ask the owner to enable it again.",
  unverified: "Google hasn't verified this account's email address, so it can't sign in.",
  error: "Couldn't sign you in. Try again.",
};

/** Shown instead of the app while login is on and there's no session (or a request came back 401). */
export default function SignInPage() {
  const [params] = useSearchParams();
  const outcome = AUTH_OUTCOMES[params.get('auth') ?? ''];
  return (
    <main className="sign-in">
      <div className="sign-in-card card">
        <Flower variant="mark" size={56} />
        <h1>Strelitzia</h1>
        <p className="muted">Sign in to see your jobs.</p>
        {outcome && <p className="notice error" role="alert">{outcome}</p>}
        <a className="button" href={LOGIN_URL}>Sign in with Google</a>
      </div>
    </main>
  );
}
