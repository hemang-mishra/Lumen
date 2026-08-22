import type { ReactNode } from 'react';
import { Navigate, Route, Routes } from 'react-router-dom';
import { readySections, SECTIONS, type Section } from '@/shell/sections';
import { Home } from './Home';
import { KitchenSink } from './KitchenSink';
import { NotFound } from './NotFound';

/**
 * Which addresses this app answers.
 *
 * A section's screen is registered here, and only a section marked as built
 * gets a route at all. That is what makes the navigation honest in both
 * directions: a screen that does not exist is not listed, and it cannot be
 * reached by typing its address either.
 *
 * A goal that builds a screen adds one line to the register below and flips
 * one mark in the section list. Nothing else moves.
 */

/** Where the screen for each built section comes from. */
const SCREENS: Record<string, () => ReactNode> = {
  // Goals 25 to 31 add their screens here, beside the section they belong to.
};

/** The address somebody lands on. The first built section, or the placeholder. */
export function landingPath(sections: readonly Section[] = SECTIONS): string {
  return readySections(sections)[0]?.path ?? '/home';
}

/** The sections that are both marked as built and actually have a screen. */
export function routableSections(sections: readonly Section[] = SECTIONS): Section[] {
  return readySections(sections).filter((section) => SCREENS[section.id]);
}

export function AppRoutes() {
  return (
    <Routes>
      <Route path="/" element={<Navigate to={landingPath()} replace />} />
      <Route path="/home" element={<Home />} />

      {routableSections().map((section) => {
        const Screen = SCREENS[section.id]!;
        return <Route key={section.id} path={section.path} element={<Screen />} />;
      })}

      {/*
        Every part of the design system, in every state. Deliberately not in
        the navigation and not linked from anywhere — it is how the look is
        reviewed, not a place anybody using Lumen needs to end up. It ships in
        every build all the same: a page that only exists in development is a
        page that quietly breaks in production.
      */}
      <Route path="/kitchen-sink" element={<KitchenSink />} />

      <Route path="*" element={<NotFound />} />
    </Routes>
  );
}
