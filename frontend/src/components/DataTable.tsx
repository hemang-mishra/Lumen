import type { ReactNode } from 'react';
import { cn } from '@/lib/cn';

/**
 * A table on a wide screen and a stack of cards on a narrow one.
 *
 * Both forms are built from the same list of columns, which is the whole
 * design. The usual way this goes wrong is that somebody designs the table,
 * ships it, then squeezes it onto a phone later by dropping the three columns
 * that fit least well — and those columns are exactly the ones somebody was
 * scrolling to find. Here a column cannot be dropped from one form without
 * being dropped from both, because there is only one list of them.
 *
 * The table scrolls sideways inside its own box rather than making the page
 * do it. A page that scrolls sideways on a phone is unusable in a way that is
 * hard to recover from with one thumb.
 */

export interface Column<Row> {
  /** Identifies the column. Also its key when React needs one. */
  key: string;
  /** The column heading, and the label beside the value on a card. */
  header: string;
  /** How to draw this column's value for a row. */
  render: (row: Row) => ReactNode;
  /** Numbers and durations read better against the right edge. */
  align?: 'start' | 'end';
  /**
   * Whether this column is the card's heading rather than a labelled value.
   *
   * One column per table, usually what the row is about. Without it a card is
   * a list of labels with nothing at the top saying what it describes.
   */
  primary?: boolean;
}

export interface DataTableProps<Row> {
  columns: ReadonlyArray<Column<Row>>;
  rows: ReadonlyArray<Row>;
  /** A stable identity per row. */
  rowKey: (row: Row) => string;
  /** What the table is, for anybody navigating by screen reader. */
  caption: string;
  className?: string;
}

export function DataTable<Row>({
  columns,
  rows,
  rowKey,
  caption,
  className,
}: DataTableProps<Row>) {
  return (
    <div className={className}>
      <WideForm columns={columns} rows={rows} rowKey={rowKey} caption={caption} />
      <NarrowForm columns={columns} rows={rows} rowKey={rowKey} caption={caption} />
    </div>
  );
}

/** The table, from the tablet breakpoint upwards. */
function WideForm<Row>({ columns, rows, rowKey, caption }: DataTableProps<Row>) {
  return (
    <div className="hidden overflow-x-auto md:block">
      <table className="w-full border-collapse text-[length:var(--density-text)]">
        <caption className="sr-only">{caption}</caption>
        <thead>
          <tr className="border-b border-border-hairline">
            {columns.map((column) => (
              <th
                key={column.key}
                scope="col"
                className={cn(
                  'py-[var(--row-padding)] pr-4 font-medium text-text-secondary',
                  column.align === 'end' ? 'text-right' : 'text-left',
                )}
              >
                {column.header}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={rowKey(row)} className="border-b border-border-hairline last:border-0">
              {columns.map((column) => (
                <td
                  key={column.key}
                  className={cn(
                    'py-[var(--row-padding)] pr-4 align-top',
                    column.align === 'end' ? 'text-right' : 'text-left',
                  )}
                >
                  {column.render(row)}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** The same rows as cards, below the tablet breakpoint. */
function NarrowForm<Row>({ columns, rows, rowKey, caption }: DataTableProps<Row>) {
  const heading = columns.find((column) => column.primary);
  const rest = columns.filter((column) => column !== heading);

  return (
    <ul className="flex list-none flex-col gap-3 p-0 md:hidden" aria-label={caption}>
      {rows.map((row) => (
        <li
          key={rowKey(row)}
          className="rounded-card border border-border-hairline bg-surface p-[var(--card-padding)] shadow-[var(--shadow-1)]"
        >
          {heading ? (
            <div className="mb-2 text-[length:var(--density-text)] font-medium text-text">
              {heading.render(row)}
            </div>
          ) : null}
          <dl className="m-0 flex flex-col gap-2">
            {rest.map((column) => (
              <div key={column.key} className="flex justify-between gap-4">
                <dt className="text-[length:var(--type-meta)] text-text-secondary">
                  {column.header}
                </dt>
                <dd className="m-0 text-right text-[length:var(--type-meta)] text-text">
                  {column.render(row)}
                </dd>
              </div>
            ))}
          </dl>
        </li>
      ))}
    </ul>
  );
}
