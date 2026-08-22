import { forwardRef, useId, type InputHTMLAttributes, type ReactNode, type TextareaHTMLAttributes } from 'react';
import { cn } from '@/lib/cn';

/**
 * A labelled thing to type into, with its description and its error.
 *
 * The wiring is the point. A label, a hint and an error message are of no use
 * to a screen reader unless the input actually points at them, and doing that
 * by hand at every call site is how half of them end up unwired. Here the
 * identifiers are generated and connected once.
 *
 * An error is announced when it appears, because somebody who has just
 * pressed save and cannot see the red text needs to be told why nothing
 * happened.
 */

const control = [
  'w-full rounded-control bg-surface text-text',
  'border border-border-strong',
  'px-3 py-2 text-[length:var(--density-text)]',
  'placeholder:text-text-tertiary',
  'transition-colors duration-[var(--dur-micro)]',
  'disabled:opacity-[var(--state-disabled-opacity)]',
].join(' ');

const invalid = 'border-critical';

export interface FieldProps {
  /** What this is, in words. Always visible — a placeholder is not a label. */
  label: string;
  /** An optional line under the label explaining what is wanted. */
  description?: string;
  /** What is wrong, if anything is. */
  error?: string;
  /** Whether an answer is required, shown as a word rather than an asterisk. */
  required?: boolean;
  /** Takes the identifiers to wire itself up with. */
  children: (wiring: FieldWiring) => ReactNode;
  className?: string;
}

/** The identifiers an input needs so its label and messages reach it. */
export interface FieldWiring {
  id: string;
  'aria-describedby'?: string;
  'aria-invalid'?: true;
  required?: boolean;
}

export function Field({ label, description, error, required, children, className }: FieldProps) {
  const id = useId();
  const describedBy = [description ? `${id}-description` : '', error ? `${id}-error` : '']
    .filter(Boolean)
    .join(' ');

  return (
    <div className={cn('flex flex-col gap-2', className)}>
      <label htmlFor={id} className="text-[length:var(--density-text)] font-medium text-text">
        {label}
        {required ? <span className="ml-1 text-text-secondary">(required)</span> : null}
      </label>

      {description ? (
        <p id={`${id}-description`} className="text-[length:var(--type-meta)] text-text-secondary">
          {description}
        </p>
      ) : null}

      {children({
        id,
        ...(describedBy ? { 'aria-describedby': describedBy } : {}),
        ...(error ? { 'aria-invalid': true as const } : {}),
        ...(required ? { required: true } : {}),
      })}

      {error ? (
        <p
          id={`${id}-error`}
          // Announced as it appears, for anybody who cannot see it turn red.
          role="alert"
          className="text-[length:var(--type-meta)] text-critical"
        >
          {error}
        </p>
      ) : null}
    </div>
  );
}

export type InputProps = InputHTMLAttributes<HTMLInputElement>;

export const Input = forwardRef<HTMLInputElement, InputProps>(function Input(
  { className, ...rest },
  ref,
) {
  return (
    <input
      ref={ref}
      className={cn(control, rest['aria-invalid'] ? invalid : '', className)}
      {...rest}
    />
  );
});

export type TextareaProps = TextareaHTMLAttributes<HTMLTextAreaElement>;

export const Textarea = forwardRef<HTMLTextAreaElement, TextareaProps>(function Textarea(
  { className, rows = 4, ...rest },
  ref,
) {
  return (
    <textarea
      ref={ref}
      rows={rows}
      className={cn(control, 'resize-y', rest['aria-invalid'] ? invalid : '', className)}
      {...rest}
    />
  );
});
