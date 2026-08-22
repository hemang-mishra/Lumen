import { SECTIONS } from '@/shell/sections';

/**
 * What somebody sees before any screen has been built.
 *
 * A blank page with an empty sidebar reads as something broken, and this app
 * will be opened in exactly that state while the surfaces are being written.
 * So it says plainly what is here, what is not, and when the rest arrives.
 *
 * It stops being reachable the moment the first real screen exists, because
 * the app then opens onto that instead.
 */
export function Home() {
  const waiting = SECTIONS.filter((section) => !section.ready);

  return (
    <div className="flex flex-col gap-8">
      <div className="flex flex-col gap-3">
        <h1 className="text-[length:var(--type-page)] leading-[var(--type-page-line)]">
          The foundation is in place
        </h1>
        <p className="max-w-[var(--measure-reading)] text-text-secondary">
          The design system, the shell and the typed connection to the service are built. The
          screens arrive one at a time, each in its own goal.
        </p>
      </div>

      <div className="flex flex-col gap-3">
        <h2 className="text-[length:var(--type-title)] leading-[var(--type-title-line)]">
          Still to come
        </h2>
        <ul className="m-0 flex list-none flex-col gap-2 p-0">
          {waiting.map((section) => (
            <li key={section.id} className="flex justify-between gap-4 text-[length:var(--type-meta)]">
              <span className="text-text">{section.label}</span>
              <span className="text-text-secondary">goal {section.goal}</span>
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}
