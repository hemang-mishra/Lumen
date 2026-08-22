import { NavLink } from 'react-router-dom';
import { cn } from '@/lib/cn';
import { Icon } from '@/components/Icon';
import { GROUPS, SECTIONS, readySections, sectionsInGroup, type Section } from './sections';

/**
 * The navigation down the side of a wide screen.
 *
 * Reflecting and inspecting are separated into labelled groups rather than
 * being one list, because they are two different products sharing a shell:
 * somebody writing in their journal should never trip over a stage payload,
 * and somebody chasing a decision should not have to leave to find one.
 *
 * The current item is the one place besides the primary action and the focus
 * ring where the accent colour is spent.
 */

export function Nav({
  className,
  sections = SECTIONS,
}: {
  className?: string;
  /** Which sections to draw. The whole list unless a caller narrows it. */
  sections?: readonly Section[];
}) {
  const built = readySections(sections);

  return (
    <nav aria-label="Sections" className={cn('flex flex-col gap-6', className)}>
      {built.length === 0 ? <NothingBuiltYet /> : null}

      {GROUPS.map(({ group, label }) => {
        const inGroup = sectionsInGroup(group, sections);
        if (inGroup.length === 0) return null;

        return (
          <div key={group} className="flex flex-col gap-1">
            <h2 className="px-3 text-[length:var(--type-meta)] font-medium text-text-secondary">
              {label}
            </h2>
            {inGroup.map((section) => (
              <NavItem key={section.id} section={section} />
            ))}
          </div>
        );
      })}
    </nav>
  );
}

/** One entry: an icon, a word, and a pill when it is the current one. */
export function NavItem({ section }: { section: Section }) {
  return (
    <NavLink
      to={section.path}
      className={({ isActive }) =>
        cn(
          'flex items-center gap-3 rounded-pill px-3 py-2',
          'text-[length:var(--density-text)] transition-colors duration-[var(--dur-micro)]',
          'min-h-[var(--control-height)]',
          isActive
            ? 'bg-accent-quiet font-medium text-accent'
            : 'text-text hover:bg-[var(--state-hover)] active:bg-[var(--state-press)]',
        )
      }
    >
      <Icon as={section.icon} />
      <span>{section.label}</span>
    </NavLink>
  );
}

/**
 * What the navigation says while there is nothing in it.
 *
 * An empty sidebar with no explanation reads as something broken. This is the
 * honest version: the foundation is here, the screens are not yet.
 */
function NothingBuiltYet() {
  return (
    <p className="px-3 text-[length:var(--type-meta)] text-text-secondary">
      No screens have been built yet. The design system and the shell are in place; the
      surfaces arrive one goal at a time.
    </p>
  );
}
