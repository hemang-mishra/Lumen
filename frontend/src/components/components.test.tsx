import { describe, expect, it, vi } from 'vitest';
import { screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { Download } from 'lucide-react';
import { renderWithProviders } from '@/test/render';
import { LumenError } from '@/api/errors';
import { Button, IconButton } from './Button';
import { Chip } from './Chip';
import { DataTable, type Column } from './DataTable';
import { Disclosure } from './Disclosure';
import { Field, Input, Textarea } from './Field';
import { Note } from './Note';
import { Overlay } from './Overlay';
import { PayloadBlock, asPayloadText } from './PayloadBlock';
import { StateBoundary, messageFor, reasonFrom, type StateSentences } from './StateBoundary';

/**
 * Tests for the reusable parts of the interface.
 *
 * They check behaviour and the things a screen reader depends on, not
 * appearance — how something looks is reviewed on the kitchen sink page in
 * both themes, which is a job for a browser rather than for this.
 */

const SENTENCES: StateSentences = {
  loading: 'Looking for runs.',
  empty: 'Nothing has been processed yet.',
  filteredEmpty: 'No run matches these filters.',
  failed: 'The runs could not be loaded.',
};

describe('buttons', () => {
  it('does not submit a form unless it is asked to', () => {
    // A button labelled "Show more" inside a form submitting it is a
    // surprise nobody wants.
    renderWithProviders(<Button>Show more</Button>);

    expect(screen.getByRole('button', { name: 'Show more' })).toHaveAttribute('type', 'button');
  });

  it('can still be a submit button when that is the point', () => {
    renderWithProviders(<Button type="submit">Save</Button>);

    expect(screen.getByRole('button', { name: 'Save' })).toHaveAttribute('type', 'submit');
  });

  it('cannot be pressed while disabled', async () => {
    const pressed = vi.fn();
    renderWithProviders(
      <Button disabled onClick={pressed}>
        Run it
      </Button>,
    );

    await userEvent.click(screen.getByRole('button', { name: 'Run it' }));

    expect(pressed).not.toHaveBeenCalled();
  });

  it('renders as whatever it wraps when asked', () => {
    renderWithProviders(
      <Button asChild>
        <a href="/somewhere">Go there</a>
      </Button>,
    );

    expect(screen.getByRole('link', { name: 'Go there' })).toBeInTheDocument();
  });

  it('gives an icon-only button words', () => {
    // An icon with no label does not exist for anybody using a screen reader.
    renderWithProviders(<IconButton icon={Download} label="Download the export" />);

    expect(screen.getByRole('button', { name: 'Download the export' })).toBeInTheDocument();
  });
});

describe('chips', () => {
  it('always carries its word, so colour is never the only meaning', () => {
    renderWithProviders(<Chip tone="critical">failed</Chip>);

    expect(screen.getByText('failed')).toBeInTheDocument();
  });
});

describe('fields', () => {
  it('connects the label to the thing being typed into', () => {
    renderWithProviders(<Field label="Session label">{(wiring) => <Input {...wiring} />}</Field>);

    expect(screen.getByLabelText('Session label')).toBeInTheDocument();
  });

  it('connects the description to it as well', () => {
    renderWithProviders(
      <Field label="Session label" description="What this conversation is filed as.">
        {(wiring) => <Input {...wiring} />}
      </Field>,
    );

    expect(screen.getByLabelText('Session label')).toHaveAccessibleDescription(
      'What this conversation is filed as.',
    );
  });

  it('announces an error and marks the field as wrong', () => {
    renderWithProviders(
      <Field label="Session label" error="This cannot be left empty.">
        {(wiring) => <Input {...wiring} />}
      </Field>,
    );

    expect(screen.getByRole('alert')).toHaveTextContent('This cannot be left empty.');
    expect(screen.getByLabelText('Session label')).toHaveAttribute('aria-invalid', 'true');
  });

  it('says required in a word rather than an asterisk', () => {
    renderWithProviders(
      <Field label="Session label" required>
        {(wiring) => <Input {...wiring} />}
      </Field>,
    );

    expect(screen.getByText('(required)')).toBeInTheDocument();
  });

  it('works the same way for something longer', async () => {
    renderWithProviders(
      <Field label="How it should behave">{(wiring) => <Textarea {...wiring} />}</Field>,
    );

    await userEvent.type(screen.getByLabelText('How it should behave'), 'plainly');

    expect(screen.getByLabelText('How it should behave')).toHaveValue('plainly');
  });
});

describe('the four ways a list can be empty', () => {
  it('shows the list when there is one', () => {
    renderWithProviders(
      <StateBoundary status="ready" sentences={SENTENCES}>
        <p>Two runs</p>
      </StateBoundary>,
    );

    expect(screen.getByText('Two runs')).toBeInTheDocument();
  });

  it('says something different for each state', () => {
    // The whole point of the component: an empty box for all four is a wrong
    // answer that looks right.
    const said = new Set(
      (['loading', 'empty', 'filtered-empty', 'failed'] as const).map((status) =>
        messageFor(status, SENTENCES),
      ),
    );

    expect(said.size).toBe(4);
  });

  it('adds the service’s own reason to a failure', () => {
    const message = messageFor(
      'failed',
      SENTENCES,
      new LumenError('unavailable', 'the graph store is not running'),
    );

    expect(message).toContain('The runs could not be loaded.');
    expect(message).toContain('the graph store is not running');
  });

  it('copes with something thrown that has no reason in it', () => {
    expect(reasonFrom('a string')).toBe('');
    expect(reasonFrom(new Error('a plain error'))).toBe('a plain error');
  });

  it('announces a failure and offers to try again', async () => {
    const retry = vi.fn();
    renderWithProviders(
      <StateBoundary status="failed" sentences={SENTENCES} onRetry={retry}>
        <p>never seen</p>
      </StateBoundary>,
    );

    expect(screen.getByRole('alert')).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: 'Try again' }));

    expect(retry).toHaveBeenCalled();
  });

  it('offers to clear the filters when a filter is what emptied it', async () => {
    const clear = vi.fn();
    renderWithProviders(
      <StateBoundary status="filtered-empty" sentences={SENTENCES} onClearFilters={clear}>
        <p>never seen</p>
      </StateBoundary>,
    );

    await userEvent.click(screen.getByRole('button', { name: 'Clear the filters' }));

    expect(clear).toHaveBeenCalled();
  });

  it('says it is busy while it loads, for anybody not watching the screen', () => {
    renderWithProviders(
      <StateBoundary status="loading" sentences={SENTENCES}>
        <p>never seen</p>
      </StateBoundary>,
    );

    expect(screen.getByText('Looking for runs.').closest('[aria-busy]')).toBeInTheDocument();
  });
});

describe('a table on a wide screen and cards on a narrow one', () => {
  interface Run {
    id: string;
    entry: string;
    trigger: string;
    wrote: number;
  }

  const columns: ReadonlyArray<Column<Run>> = [
    { key: 'entry', header: 'What was processed', render: (run) => run.entry, primary: true },
    { key: 'trigger', header: 'Triggered by', render: (run) => run.trigger },
    { key: 'wrote', header: 'Records written', render: (run) => run.wrote, align: 'end' },
  ];

  const rows: Run[] = [{ id: 'job_1', entry: 'Tuesday evening', trigger: 'import', wrote: 14 }];

  function renderTable() {
    return renderWithProviders(
      <DataTable columns={columns} rows={rows} rowKey={(run) => run.id} caption="Recent runs" />,
    );
  }

  it('draws every column as a column', () => {
    renderTable();

    for (const column of columns) {
      expect(screen.getByRole('columnheader', { name: column.header })).toBeInTheDocument();
    }
  });

  it('drops no column from the card form', () => {
    // The failure this component exists to prevent: a phone quietly losing
    // the three columns that fit least well, which are the ones somebody was
    // scrolling to find.
    renderTable();
    const cards = screen.getByRole('list', { name: 'Recent runs' });

    for (const column of columns.filter((one) => !one.primary)) {
      expect(within(cards).getByText(column.header)).toBeInTheDocument();
    }
    expect(within(cards).getByText('Tuesday evening')).toBeInTheDocument();
  });

  it('tells a screen reader what the table is', () => {
    renderTable();

    expect(screen.getByRole('table', { name: 'Recent runs' })).toBeInTheDocument();
  });
});

describe('disclosure', () => {
  it('starts folded and says what is inside', async () => {
    renderWithProviders(
      <Disclosure label="What went in" hint="2 messages">
        <p>the payload</p>
      </Disclosure>,
    );

    expect(screen.queryByText('the payload')).not.toBeInTheDocument();

    await userEvent.click(screen.getByRole('button', { name: /What went in/ }));

    expect(screen.getByText('the payload')).toBeInTheDocument();
  });

  it('can start open where that is the right default', () => {
    renderWithProviders(
      <Disclosure label="Everything it holds" defaultOpen>
        <p>the records</p>
      </Disclosure>,
    );

    expect(screen.getByText('the records')).toBeInTheDocument();
  });
});

describe('payload blocks', () => {
  it('is reachable by keyboard, since it scrolls', () => {
    renderWithProviders(<PayloadBlock label="stage input">{'{"a": 1}'}</PayloadBlock>);

    expect(screen.getByRole('region', { name: 'stage input' })).toHaveAttribute('tabindex', '0');
  });

  it('expands to its full height and back', async () => {
    renderWithProviders(<PayloadBlock>{'{"a": 1}'}</PayloadBlock>);

    await userEvent.click(screen.getByRole('button', { name: 'Show all of it' }));

    expect(screen.getByRole('button', { name: 'Collapse it' })).toBeInTheDocument();
  });

  it('lays out anything that is not already text', () => {
    expect(asPayloadText({ a: 1 })).toBe('{\n  "a": 1\n}');
    expect(asPayloadText('already text')).toBe('already text');
    expect(asPayloadText(null)).toBe('');
  });

  it('says a payload could not be shown rather than showing nonsense', () => {
    const circular: Record<string, unknown> = {};
    circular.self = circular;

    expect(asPayloadText(circular)).toContain('could not be displayed');
  });
});

describe('saying what is held back', () => {
  it('puts a word beside the colour', () => {
    renderWithProviders(<Note tone="caution">One record is being held back.</Note>);

    expect(screen.getByText('One record is being held back.')).toBeInTheDocument();
    expect(screen.getByRole('img', { name: 'Held back' })).toBeInTheDocument();
  });
});

describe('the overlay', () => {
  it('is announced by its title when it opens', () => {
    renderWithProviders(
      <Overlay open onOpenChange={() => undefined} title="A sheet, or a dialog">
        <p>inside</p>
      </Overlay>,
    );

    expect(screen.getByRole('dialog', { name: 'A sheet, or a dialog' })).toBeInTheDocument();
  });

  it('closes from its own close button', async () => {
    const changed = vi.fn();
    renderWithProviders(
      <Overlay open onOpenChange={changed} title="A sheet">
        <p>inside</p>
      </Overlay>,
    );

    await userEvent.click(screen.getByRole('button', { name: 'Close' }));

    expect(changed).toHaveBeenCalledWith(false);
  });
});
