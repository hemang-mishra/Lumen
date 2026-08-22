import type { LucideIcon } from 'lucide-react';
import { cn } from '@/lib/cn';

/**
 * The one way an icon appears anywhere in the app.
 *
 * Every icon is the same size, the same stroke weight, and the colour of the
 * text beside it. A component cannot choose otherwise, which is what keeps a
 * hundred icons from slowly becoming a hundred weights.
 *
 * An icon on its own is invisible to a screen reader unless it is given
 * words, so anything meaningful must carry a label; anything decorative is
 * hidden from assistive technology instead of being announced as nothing.
 */

/** The size every icon is drawn at. */
const SIZE = 20;

/** The one stroke weight. Thin enough to read as line-style, not as a fill. */
const STROKE = 1.5;

export interface IconProps {
  /** The icon to draw. */
  as: LucideIcon;
  /** What it means, for anybody who cannot see it. Omitted if decorative. */
  label?: string;
  className?: string;
}

export function Icon({ as: Glyph, label, className }: IconProps) {
  return (
    <Glyph
      size={SIZE}
      strokeWidth={STROKE}
      className={cn('shrink-0', className)}
      aria-hidden={label ? undefined : true}
      aria-label={label}
      role={label ? 'img' : undefined}
      focusable="false"
    />
  );
}
