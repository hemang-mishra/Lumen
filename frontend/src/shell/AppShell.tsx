import type { ReactNode } from 'react';
import { useLocation } from 'react-router-dom';
import { cn } from '@/lib/cn';
import { DensityScope } from '@/theme';
import { IdentitySlot } from './IdentitySlot';
import { MobileNav } from './MobileNav';
import { Nav } from './Nav';
import { ThemeSwitch } from './ThemeSwitch';
import { sectionFor, surfaceKindOf } from './sections';

/**
 * The frame every screen sits in.
 *
 * It does three things. It keeps the two halves of the product apart, so a
 * page for writing and a page for debugging a pipeline never blur into one
 * another. It decides how densely the content is packed, from which section
 * is open and what kind of device this is. And it holds the places later
 * goals need — who is signed in, how many things are waiting for a person —
 * so those can arrive without the shell being rebuilt around them.
 *
 * On a phone the sidebar becomes a bar along the bottom, where a thumb can
 * reach it.
 */

export function AppShell({ children }: { children: ReactNode }) {
  const { pathname } = useLocation();
  const section = sectionFor(pathname);
  const kind = section ? surfaceKindOf(section.group) : 'reflect';

  return (
    <div className="flex min-h-full flex-col bg-canvas text-text">
      {/* Somebody on a keyboard should not have to walk through the whole
          navigation to reach the page they just opened. */}
      <a
        href="#content"
        className={cn(
          'sr-only focus:not-sr-only focus:absolute focus:top-2 focus:left-2 focus:z-50',
          'focus:rounded-control focus:bg-surface-raised focus:px-4 focus:py-2',
        )}
      >
        Skip to content
      </a>

      <header className="flex items-center gap-4 border-b border-border-hairline px-[var(--gutter)] py-3">
        <span className="text-[length:var(--type-title)] font-semibold tracking-[var(--tracking-tight)]">
          Lumen
        </span>
        <div className="flex-1" />
        {/* Where the count of things waiting for a person will go. Left
            empty rather than faked, so the space is proven to exist. */}
        <ThemeSwitch />
        <IdentitySlot />
      </header>

      <div className="flex flex-1">
        <aside className="hidden w-56 shrink-0 border-r border-border-hairline px-3 py-6 md:block">
          <Nav />
        </aside>

        <DensityScope kind={kind} className="min-w-0 flex-1">
          <main
            id="content"
            tabIndex={-1}
            className={cn(
              'mx-auto w-full px-[var(--gutter)] py-8',
              kind === 'inspect' ? 'max-w-[var(--width-inspect)]' : 'max-w-[var(--width-reading)]',
            )}
          >
            {children}
          </main>
        </DensityScope>
      </div>

      <MobileNav className="sticky bottom-0 md:hidden" />
    </div>
  );
}
