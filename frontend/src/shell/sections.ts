import {
  Activity,
  BarChart3,
  CalendarDays,
  FileStack,
  Inbox,
  MessageSquare,
  Network,
  Settings,
  Upload,
  type LucideIcon,
} from 'lucide-react';
import type { SurfaceKind } from '@/theme/density';

/**
 * Every section this app will have, in one list.
 *
 * The whole map is written down now, including the eight screens that do not
 * exist yet, so the shape of the product is decided once rather than
 * discovered a screen at a time. What keeps that honest is the `ready` mark:
 * only sections that really exist appear in the navigation, and nothing else
 * is reachable by typing its address either.
 *
 * A goal that builds a screen changes one line here — `ready: false` becomes
 * `ready: true` — and the screen appears in the right place, in the right
 * group, with no navigation to restructure.
 */

/** Which half of the app a section belongs to. */
export type SectionGroup =
  /** Writing, reading back, reflecting. Calm surfaces. */
  | 'reflect'
  /** Looking at what the pipeline made of it. Dense surfaces. */
  | 'inspect'
  /** Settings and diagnostics. */
  | 'system';

export interface Section {
  /** Identifies the section in code and in tests. */
  id: string;
  /** What it is called in the navigation. */
  label: string;
  icon: LucideIcon;
  group: SectionGroup;
  /** Where it lives. */
  path: string;
  /** Whether the screen behind it has actually been built. */
  ready: boolean;
  /** The goal that builds it, so this list explains its own gaps. */
  goal: number;
}

export const SECTIONS: readonly Section[] = [
  { id: 'today', label: 'Today', icon: MessageSquare, group: 'reflect', path: '/today', ready: false, goal: 28 },
  { id: 'history', label: 'History', icon: CalendarDays, group: 'reflect', path: '/history', ready: false, goal: 28 },
  { id: 'review', label: 'Review', icon: Inbox, group: 'reflect', path: '/review', ready: false, goal: 29 },
  { id: 'reports', label: 'Reports', icon: BarChart3, group: 'reflect', path: '/reports', ready: false, goal: 30 },
  { id: 'import', label: 'Import', icon: Upload, group: 'inspect', path: '/import', ready: false, goal: 25 },
  { id: 'runs', label: 'Runs', icon: Activity, group: 'inspect', path: '/runs', ready: false, goal: 25 },
  { id: 'episodes', label: 'Episodes', icon: FileStack, group: 'inspect', path: '/episodes', ready: false, goal: 26 },
  { id: 'graph', label: 'Graph', icon: Network, group: 'inspect', path: '/graph', ready: false, goal: 27 },
  { id: 'settings', label: 'Settings', icon: Settings, group: 'system', path: '/settings', ready: false, goal: 31 },
];

/** The order the groups appear in, and what each is called. */
export const GROUPS: ReadonlyArray<{ group: SectionGroup; label: string }> = [
  { group: 'reflect', label: 'Reflect' },
  { group: 'inspect', label: 'Inspect' },
  { group: 'system', label: 'System' },
];

/** The sections that have actually been built. */
export function readySections(sections: readonly Section[] = SECTIONS): Section[] {
  return sections.filter((section) => section.ready);
}

/** The built sections in one group, for drawing that part of the navigation. */
export function sectionsInGroup(
  group: SectionGroup,
  sections: readonly Section[] = SECTIONS,
): Section[] {
  return readySections(sections).filter((section) => section.group === group);
}

/**
 * Which section an address belongs to, if any.
 *
 * Matches on the start of the path so that a record inside a section still
 * lights up that section in the navigation.
 */
export function sectionFor(
  path: string,
  sections: readonly Section[] = SECTIONS,
): Section | undefined {
  return readySections(sections).find(
    (section) => path === section.path || path.startsWith(`${section.path}/`),
  );
}

/**
 * How densely a group's surfaces are packed.
 *
 * Settings sits with the calm half: it is a page of prose and controls, not a
 * table of thirty stages.
 */
export function surfaceKindOf(group: SectionGroup): SurfaceKind {
  return group === 'inspect' ? 'inspect' : 'reflect';
}
