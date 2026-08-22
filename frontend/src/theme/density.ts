/**
 * Deciding how tightly a surface is packed.
 *
 * This is a rule, not a preference. Writing surfaces are always comfortable
 * because they are for reading and thinking. Inspecting surfaces would rather
 * be compact, since a run can hold thirty stages — but only on a device with
 * a mouse. A phone showing a pipeline trace stays comfortable, because a 32px
 * target cannot be hit with a thumb whatever happens to be on the screen.
 */

/** The two densities. There is no third, and nobody chooses between them. */
export type Density = 'comfortable' | 'compact';

/** Which half of the app a surface belongs to. */
export type SurfaceKind = 'reflect' | 'inspect';

/** The question asked of the device: is there a mouse pointing at this? */
export const FINE_POINTER_QUERY = '(pointer: fine)';

/**
 * The density a surface should use.
 *
 * @param kind Whether this is somewhere to write or somewhere to inspect.
 * @param hasFinePointer Whether the device is driven by a mouse.
 */
export function resolveDensity(kind: SurfaceKind, hasFinePointer: boolean): Density {
  if (kind === 'reflect') return 'comfortable';
  return hasFinePointer ? 'compact' : 'comfortable';
}
