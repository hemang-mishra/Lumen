import { Monitor, Moon, Sun } from 'lucide-react';
import { cn } from '@/lib/cn';
import { Icon } from '@/components/Icon';
import { useTheme } from '@/theme';
import type { ThemeChoice } from '@/theme/theme';

/**
 * Choosing between following the device, light, and dark.
 *
 * Three buttons rather than a toggle, because "follow my device" is a real
 * choice and a two-state switch cannot express it — a person who has never
 * chosen is not choosing light, they are choosing whatever their morning
 * looks like.
 *
 * Each button is labelled in words as well as by its icon, and the current
 * one is announced as pressed, so the state is available without seeing which
 * of three shapes is highlighted.
 */

const CHOICES: ReadonlyArray<{ choice: ThemeChoice; label: string; icon: typeof Sun }> = [
  { choice: 'system', label: 'Follow my device', icon: Monitor },
  { choice: 'light', label: 'Light', icon: Sun },
  { choice: 'dark', label: 'Dark', icon: Moon },
];

export function ThemeSwitch({ className }: { className?: string }) {
  const { choice, setChoice } = useTheme();

  return (
    <div
      role="group"
      aria-label="Theme"
      className={cn('inline-flex items-center gap-1 rounded-pill p-1', className)}
    >
      {CHOICES.map((option) => {
        const current = option.choice === choice;
        return (
          <button
            key={option.choice}
            type="button"
            aria-pressed={current}
            aria-label={option.label}
            title={option.label}
            onClick={() => setChoice(option.choice)}
            className={cn(
              'flex size-9 cursor-pointer items-center justify-center rounded-pill',
              'transition-colors duration-[var(--dur-micro)]',
              current
                ? 'bg-accent-quiet text-accent'
                : 'text-text-secondary hover:bg-[var(--state-hover)] active:bg-[var(--state-press)]',
            )}
          >
            <Icon as={option.icon} />
          </button>
        );
      })}
    </div>
  );
}
