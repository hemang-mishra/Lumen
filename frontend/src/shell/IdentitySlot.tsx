import { useSession } from '@/shell/SessionProvider';

/**
 * Who the app is currently showing.
 *
 * Every screen in Lumen is one person's private history, so "whose data am I
 * looking at" must never be a question answered by hovering over a circle.
 * The name is written out, and the email underneath it on a wide screen,
 * because two accounts belonging to the same person will have the same name
 * on them.
 *
 * With sign-in switched off there is nobody to name, and this renders
 * nothing rather than inventing a placeholder person.
 */
export function IdentitySlot() {
  const { user } = useSession();
  if (!user) return null;

  const name = user.display_name?.trim() || user.email;

  return (
    <div className="flex flex-col items-end leading-tight">
      <span className="text-[length:var(--type-meta)] font-medium text-text">{name}</span>
      {name !== user.email ? (
        <span className="hidden text-[length:var(--type-meta)] text-text-secondary sm:block">
          {user.email}
        </span>
      ) : null}
    </div>
  );
}
