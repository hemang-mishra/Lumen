import { useState } from 'react';
import { NavLink } from 'react-router-dom';
import { Menu } from 'lucide-react';
import { cn } from '@/lib/cn';
import { Icon } from '@/components/Icon';
import { IconButton } from '@/components/Button';
import { Overlay } from '@/components/Overlay';
import { Nav } from './Nav';
import { SECTIONS, sectionsInGroup, type Section } from './sections';

/**
 * Navigation on a phone: a bar along the bottom, and everything else in a sheet.
 *
 * The bar holds the writing surfaces, because those are the ones somebody
 * reaches for one-handed while walking, and the bottom of the screen is the
 * only part of a phone a thumb can reach comfortably. Everything else is
 * behind the menu — reachable, but not competing for the four places worth
 * having.
 *
 * The bar sits above the safe area, so it clears the home indicator rather
 * than being half underneath it.
 */

/** The most entries the bar holds before it stops being tappable. */
const BAR_LIMIT = 4;

export function MobileNav({
  className,
  sections = SECTIONS,
}: {
  className?: string;
  /** Which sections to draw. The whole list unless a caller narrows it. */
  sections?: readonly Section[];
}) {
  const [menuOpen, setMenuOpen] = useState(false);
  const inTheBar = sectionsInGroup('reflect', sections).slice(0, BAR_LIMIT);

  return (
    <>
      <div
        className={cn(
          'flex items-center justify-around gap-1 border-t border-border-hairline bg-surface',
          'px-2 pt-1 pb-[calc(var(--space-4)+env(safe-area-inset-bottom))]',
          className,
        )}
      >
        {inTheBar.map((section) => (
          <BarItem key={section.id} section={section} />
        ))}
        <IconButton
          icon={Menu}
          label="All sections"
          onClick={() => setMenuOpen(true)}
          className="flex-1"
        />
      </div>

      <Overlay
        open={menuOpen}
        onOpenChange={setMenuOpen}
        title="Sections"
        description="Everywhere you can go in Lumen."
      >
        {/* The same navigation as the sidebar, so there is one list of
            sections and not a second one to keep in step. */}
        <div onClick={() => setMenuOpen(false)}>
          <Nav sections={sections} />
        </div>
      </Overlay>
    </>
  );
}

/** One entry in the bottom bar: a big target, an icon, and its word under it. */
function BarItem({ section }: { section: Section }) {
  return (
    <NavLink
      to={section.path}
      className={({ isActive }) =>
        cn(
          'flex min-h-11 flex-1 flex-col items-center justify-center gap-1 rounded-control px-2 py-1',
          'text-[length:var(--type-meta)]',
          isActive ? 'text-accent' : 'text-text-secondary',
        )
      }
    >
      <Icon as={section.icon} />
      <span>{section.label}</span>
    </NavLink>
  );
}
