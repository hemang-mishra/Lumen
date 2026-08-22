import { describe, expect, it } from 'vitest';
import { act, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { renderWithProviders } from '@/test/render';
import { setMediaQueries } from '@/test/setup';
import { Session, type SessionView, type User } from '@/api/session';
import { AppShell } from './AppShell';
import { IdentitySlot } from './IdentitySlot';
import { MobileNav } from './MobileNav';
import { Nav } from './Nav';
import { ThemeSwitch } from './ThemeSwitch';
import {
  readySections,
  SECTIONS,
  sectionFor,
  sectionsInGroup,
  surfaceKindOf,
  type Section,
} from './sections';

/**
 * Tests for the frame every screen sits in.
 *
 * The section list is where most of the value is. It is what makes the
 * navigation honest — a screen that has not been built is not offered — and
 * what lets a later goal add one without rearranging anything.
 */

/** A list where two sections have been built and one has not. */
const BUILT: Section[] = [
  { ...find('today'), ready: true },
  { ...find('runs'), ready: true },
  { ...find('reports'), ready: false },
];

function find(id: string): Section {
  const section = SECTIONS.find((one) => one.id === id);
  if (!section) throw new Error(`there is no section called ${id}`);
  return section;
}

describe('the list of sections', () => {
  it('offers only what has been built', () => {
    expect(readySections(BUILT).map((section) => section.id)).toEqual(['today', 'runs']);
  });

  it('keeps the two halves of the product apart', () => {
    expect(sectionsInGroup('reflect', BUILT).map((section) => section.id)).toEqual(['today']);
    expect(sectionsInGroup('inspect', BUILT).map((section) => section.id)).toEqual(['runs']);
  });

  it('finds the section an address belongs to', () => {
    expect(sectionFor('/runs', BUILT)?.id).toBe('runs');
  });

  it('keeps a record inside a section belonging to that section', () => {
    expect(sectionFor('/runs/job_1', BUILT)?.id).toBe('runs');
  });

  it('finds nothing for an address in a section nobody has built', () => {
    expect(sectionFor('/reports', BUILT)).toBeUndefined();
  });

  it('packs inspect surfaces tightly and everything else comfortably', () => {
    expect(surfaceKindOf('inspect')).toBe('inspect');
    expect(surfaceKindOf('reflect')).toBe('reflect');
    expect(surfaceKindOf('system')).toBe('reflect');
  });

  it('says which goal builds each screen that is missing', () => {
    for (const section of SECTIONS.filter((one) => !one.ready)) {
      expect(section.goal, `${section.id} does not say which goal builds it`).toBeGreaterThan(23);
    }
  });

  it('gives every section a unique address', () => {
    const paths = SECTIONS.map((section) => section.path);

    expect(new Set(paths).size).toBe(paths.length);
  });
});

describe('the navigation', () => {
  it('explains itself while there is nothing in it', () => {
    // An empty sidebar with no explanation reads as something broken, and
    // this app will be in exactly that state for several goals.
    renderWithProviders(<Nav />);

    expect(screen.getByText(/No screens have been built yet/)).toBeInTheDocument();
  });

  it('is labelled, so it can be skipped to and skipped over', () => {
    renderWithProviders(<Nav />);

    expect(screen.getByRole('navigation', { name: 'Sections' })).toBeInTheDocument();
  });

  it('draws each built section under the heading for its half', () => {
    renderWithProviders(<Nav sections={BUILT} />);

    expect(screen.getByRole('heading', { name: 'Reflect' })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'Inspect' })).toBeInTheDocument();
    expect(screen.getByRole('link', { name: /Today/ })).toHaveAttribute('href', '/today');
    expect(screen.getByRole('link', { name: /Runs/ })).toHaveAttribute('href', '/runs');
  });

  it('leaves out a section nobody has built', () => {
    renderWithProviders(<Nav sections={BUILT} />);

    expect(screen.queryByRole('link', { name: /Reports/ })).not.toBeInTheDocument();
  });

  it('marks where you currently are', () => {
    renderWithProviders(<Nav sections={BUILT} />, { route: '/runs' });

    expect(screen.getByRole('link', { name: /Runs/ })).toHaveAttribute('aria-current', 'page');
  });

  it('stops explaining itself once something has been built', () => {
    renderWithProviders(<Nav sections={BUILT} />);

    expect(screen.queryByText(/No screens have been built yet/)).not.toBeInTheDocument();
  });
});

describe('the navigation on a phone', () => {
  it('puts the writing surfaces where a thumb can reach them', () => {
    renderWithProviders(<MobileNav sections={BUILT} />);

    expect(screen.getByRole('link', { name: /Today/ })).toBeInTheDocument();
    // Inspect surfaces are reachable, but not in the four places worth having.
    expect(screen.queryByRole('link', { name: /Runs/ })).not.toBeInTheDocument();
  });

  it('holds everything else behind one menu', async () => {
    renderWithProviders(<MobileNav sections={BUILT} />);

    await userEvent.click(screen.getByRole('button', { name: 'All sections' }));

    const menu = screen.getByRole('dialog', { name: 'Sections' });
    expect(within(menu).getByRole('link', { name: /Runs/ })).toBeInTheDocument();
  });

  it('closes the menu once somewhere has been chosen', async () => {
    renderWithProviders(<MobileNav sections={BUILT} />);
    await userEvent.click(screen.getByRole('button', { name: 'All sections' }));

    const menu = screen.getByRole('dialog', { name: 'Sections' });
    await userEvent.click(within(menu).getByRole('link', { name: /Runs/ }));

    await waitFor(() =>
      expect(screen.queryByRole('dialog', { name: 'Sections' })).not.toBeInTheDocument(),
    );
  });
});

describe('the shell', () => {
  it('lets a keyboard skip straight to the page', () => {
    renderWithProviders(
      <AppShell>
        <p>a screen</p>
      </AppShell>,
    );

    expect(screen.getByRole('link', { name: 'Skip to content' })).toHaveAttribute(
      'href',
      '#content',
    );
  });

  it('packs a writing surface comfortably even where there is a mouse', () => {
    setMediaQueries({ '(pointer: fine)': true });
    const { container } = renderWithProviders(
      <AppShell>
        <p>a screen</p>
      </AppShell>,
      { route: '/home' },
    );

    expect(container.querySelector('[data-density]')).toHaveAttribute(
      'data-density',
      'comfortable',
    );
  });

  it('holds the theme switch and a place for who is signed in', () => {
    renderWithProviders(
      <AppShell>
        <p>a screen</p>
      </AppShell>,
    );

    expect(screen.getByRole('group', { name: 'Theme' })).toBeInTheDocument();
  });
});

describe('the theme switch', () => {
  it('offers all three choices, each in words', () => {
    renderWithProviders(<ThemeSwitch />);

    for (const label of ['Follow my device', 'Light', 'Dark']) {
      expect(screen.getByRole('button', { name: label })).toBeInTheDocument();
    }
  });

  it('says which one is current without relying on a colour', async () => {
    renderWithProviders(<ThemeSwitch />);

    await userEvent.click(screen.getByRole('button', { name: 'Dark' }));

    expect(screen.getByRole('button', { name: 'Dark' })).toHaveAttribute('aria-pressed', 'true');
    expect(screen.getByRole('button', { name: 'Light' })).toHaveAttribute('aria-pressed', 'false');
  });
});

describe('who the app is showing', () => {
  const SOMEBODY: User = {
    user_id: 'usr_1',
    email: 'somebody@example.com',
    display_name: 'Somebody',
    status: 'ACTIVE',
  };

  function sessionView(user: User): SessionView {
    return { access_token: 't', expires_in: 900, token_type: 'Bearer', user };
  }

  function renderWith(session: Session) {
    return renderWithProviders(<IdentitySlot />, { session });
  }

  it('names them, rather than showing a circle to hover over', () => {
    const session = new Session(async () => sessionView(SOMEBODY));
    session.begin(sessionView(SOMEBODY));

    renderWith(session);

    expect(screen.getByText('Somebody')).toBeInTheDocument();
    expect(screen.getByText('somebody@example.com')).toBeInTheDocument();
  });

  it('falls back to the email where there is no name', () => {
    const nameless = { ...SOMEBODY, display_name: '' };
    const session = new Session(async () => sessionView(nameless));
    session.begin(sessionView(nameless));

    renderWith(session);

    expect(screen.getByText('somebody@example.com')).toBeInTheDocument();
  });

  it('shows nobody when this deployment has no sign-in', () => {
    const session = new Session(async () => sessionView(SOMEBODY));

    const { container } = renderWith(session);

    expect(container.textContent).toBe('');
  });

  it('stops naming somebody the moment they sign out', async () => {
    const session = new Session(async () => sessionView(SOMEBODY));
    session.begin(sessionView(SOMEBODY));
    renderWith(session);

    act(() => session.end('requested'));

    await waitFor(() => expect(screen.queryByText('Somebody')).not.toBeInTheDocument());
  });
});
