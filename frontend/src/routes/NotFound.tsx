import { Link } from 'react-router-dom';
import { useLocation } from 'react-router-dom';

/**
 * An address this app does not answer.
 *
 * It says which address, because most of the time somebody arrived here from
 * a link they were given rather than by typing, and the address is the only
 * thing that tells them where it came from. It does not guess at what they
 * meant.
 */
export function NotFound() {
  const { pathname } = useLocation();

  return (
    <div className="flex flex-col items-start gap-4">
      <h1 className="text-[length:var(--type-page)] leading-[var(--type-page-line)]">
        There is nothing at this address
      </h1>
      <p className="text-text-secondary">
        Nothing in Lumen answers <code className="font-mono">{pathname}</code>. It may belong
        to a screen that has not been built yet.
      </p>
      <Link to="/" className="text-accent underline-offset-2 hover:underline">
        Back to the start
      </Link>
    </div>
  );
}
