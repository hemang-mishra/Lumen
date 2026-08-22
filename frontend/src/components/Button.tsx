import { forwardRef, type ButtonHTMLAttributes, type ReactNode } from 'react';
import { Slot, Slottable } from '@radix-ui/react-slot';
import type { LucideIcon } from 'lucide-react';
import { cn } from '@/lib/cn';
import { Icon } from './Icon';

/**
 * The three buttons this app has.
 *
 * Primary is the accent one and there is at most one on a view. Secondary is
 * a surface with a hairline. Ghost is text that happens to be clickable.
 *
 * Destructive actions are a secondary or a ghost with critical-coloured text,
 * never a large red fill. A screen about somebody's inner life should not
 * shout at them, and a red block is the loudest thing an interface can do.
 *
 * Hover and press are the same translucent layer every other control uses, so
 * a button put next to a navigation item or a chip behaves identically.
 */

export type ButtonVariant = 'primary' | 'secondary' | 'ghost';
export type ButtonTone = 'default' | 'critical';

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant;
  tone?: ButtonTone;
  /** An icon before the label. Decorative — the label already says it. */
  icon?: LucideIcon;
  /** Render as whatever child was given, so a link can look like a button. */
  asChild?: boolean;
  children?: ReactNode;
}

/** Shared by all three: the shape, the focus ring, the disabled treatment. */
const base = [
  'relative inline-flex items-center justify-center gap-2',
  'h-[var(--control-height)] px-4 rounded-control',
  'text-[length:var(--density-text)] font-medium whitespace-nowrap',
  'transition-colors duration-[var(--dur-micro)] ease-[var(--ease-enter)]',
  'disabled:opacity-[var(--state-disabled-opacity)] disabled:pointer-events-none',
  'cursor-pointer',
].join(' ');

/** The one state layer, expressed as the overlay each surface sits under. */
const layer = 'hover:bg-[var(--state-hover)] active:bg-[var(--state-press)]';

const variants: Record<ButtonVariant, string> = {
  primary: 'bg-accent text-accent-contrast hover:brightness-110 active:brightness-95',
  secondary: `bg-surface text-text border border-border-strong ${layer}`,
  ghost: `bg-transparent text-text ${layer}`,
};

const tones: Record<ButtonTone, string> = {
  default: '',
  critical: 'text-critical',
};

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(function Button(
  { variant = 'secondary', tone = 'default', icon, asChild, className, children, type, ...rest },
  ref,
) {
  const Element = asChild ? Slot : 'button';

  return (
    <Element
      ref={ref}
      // A button inside a form submits it unless told otherwise, which is a
      // surprise nobody wants from a button labelled "Show more".
      type={asChild ? undefined : (type ?? 'button')}
      className={cn(base, variants[variant], tones[tone], className)}
      {...rest}
    >
      {icon ? <Icon as={icon} /> : null}
      {/* Marks which part is the child being rendered as, so a button can
          still have an icon of its own when it is standing in for a link. */}
      <Slottable>{children}</Slottable>
    </Element>
  );
});

export interface IconButtonProps extends Omit<ButtonProps, 'icon' | 'children'> {
  /** The icon to show. */
  icon: LucideIcon;
  /** What pressing it does. Required — an icon alone says nothing out loud. */
  label: string;
}

/**
 * A button that is only an icon.
 *
 * The label is not optional. An icon-only control with no words is a control
 * that does not exist for anybody using a screen reader, and it is the single
 * easiest accessibility failure to ship without noticing.
 */
export const IconButton = forwardRef<HTMLButtonElement, IconButtonProps>(function IconButton(
  { icon, label, variant = 'ghost', className, ...rest },
  ref,
) {
  return (
    <Button
      ref={ref}
      variant={variant}
      aria-label={label}
      title={label}
      className={cn('w-[var(--control-height)] px-0', className)}
      {...rest}
    >
      <Icon as={icon} />
    </Button>
  );
});
