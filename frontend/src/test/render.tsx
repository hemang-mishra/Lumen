import type { ReactElement, ReactNode } from 'react';
import { render, type RenderOptions } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import { Session } from '@/api/session';
import { SessionProvider } from '@/shell/SessionProvider';

/**
 * Rendering a component with the things it can assume are around it.
 *
 * A router, because half the components link somewhere. A cache, because
 * anything that fetches needs one. A session, because the shell always has
 * one — with nobody signed in unless a test says otherwise, which is the
 * state the app is in with sign-in switched off.
 *
 * All three are built fresh for each test, so nothing cached or remembered
 * in one test can be read by the next.
 */
export function renderWithProviders(
  ui: ReactElement,
  {
    route = '/',
    session = new Session(async () => {
      throw new Error('this test did not expect the session to be renewed');
    }),
    ...options
  }: RenderOptions & { route?: string; session?: Session } = {},
) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });

  function Wrapper({ children }: { children: ReactNode }) {
    return (
      <QueryClientProvider client={client}>
        <SessionProvider session={session}>
          <MemoryRouter initialEntries={[route]}>{children}</MemoryRouter>
        </SessionProvider>
      </QueryClientProvider>
    );
  }

  return { client, session, ...render(ui, { wrapper: Wrapper, ...options }) };
}
