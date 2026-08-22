import type { ReactNode } from 'react';
import { useMediaQuery } from '@/lib/useMediaQuery';
import { FINE_POINTER_QUERY, resolveDensity, type SurfaceKind } from './density';

/**
 * Wraps a part of the app in the density that part should use.
 *
 * The density is written on the element as an attribute and everything
 * inside inherits it through CSS, so no component has to be told which one it
 * is in — a button is the right height wherever it is put.
 */
export function DensityScope({
  kind,
  children,
  className,
}: {
  kind: SurfaceKind;
  children: ReactNode;
  className?: string;
}): ReactNode {
  const finePointer = useMediaQuery(FINE_POINTER_QUERY);
  const density = resolveDensity(kind, finePointer);

  return (
    <div data-density={density} data-surface={kind} className={className}>
      {children}
    </div>
  );
}
