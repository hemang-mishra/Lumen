import { QueryClientProvider, type QueryClient } from '@tanstack/react-query';
import { BrowserRouter } from 'react-router-dom';
import { lumen } from '@/api/client';
import { createQueryClient } from '@/api/query';
import type { LumenClient } from '@/api/client';
import { AppShell } from '@/shell/AppShell';
import { SessionProvider } from '@/shell/SessionProvider';
import { ThemeProvider } from '@/theme';
import { AppRoutes } from '@/routes/routes';

/**
 * The app, and the four things every screen inside it can rely on.
 *
 * The theme is settled, the session is known, the cache is tied to whoever
 * that is, and there is somewhere to navigate. Each is passed in rather than
 * reached for, so a test can assemble the same app around its own service,
 * its own cache and its own session without any of them being real.
 */
export function App({
  client = lumen,
  queryClient,
}: {
  client?: LumenClient;
  queryClient?: QueryClient;
}) {
  const cache = queryClient ?? createQueryClient();

  return (
    <QueryClientProvider client={cache}>
      <SessionProvider session={client.session}>
        <ThemeProvider>
          <BrowserRouter>
            <AppShell>
              <AppRoutes />
            </AppShell>
          </BrowserRouter>
        </ThemeProvider>
      </SessionProvider>
    </QueryClientProvider>
  );
}
