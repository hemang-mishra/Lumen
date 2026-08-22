import { useState, type ReactNode } from 'react';
import { Download, Trash2 } from 'lucide-react';
import {
  Button,
  Chip,
  DataTable,
  Disclosure,
  Field,
  IconButton,
  Input,
  Note,
  Overlay,
  PayloadBlock,
  StateBoundary,
  Textarea,
  asPayloadText,
  type Column,
  type ListStatus,
} from '@/components';
import { JournalText, RecordLine } from '@/patterns';
import { LumenError } from '@/api/errors';
import { formatDate, formatDuration } from '@/lib/format';

/**
 * Every part of the design system, in every state it has.
 *
 * A goal that builds a foundation has nothing to demonstrate otherwise, and
 * "we reviewed it in dark mode on a laptop" is how half a design system's
 * states end up never being looked at. This page is what the review is done
 * against, and what the automated checks walk through: both themes, both
 * densities, a phone width, a keyboard, and reduced motion.
 *
 * Everything here is made up. It calls nothing and fetches nothing.
 */

/** A made-up run, so the table has something with the right shape in it. */
interface DemoRun {
  id: string;
  entry: string;
  trigger: string;
  wrote: number;
  took: number;
  when: string;
}

const RUNS: DemoRun[] = [
  {
    id: 'job_2026_06_11_01',
    entry: 'Tuesday evening, after the review',
    trigger: 'live session',
    wrote: 14,
    took: 184_000,
    when: '2026-06-11T19:04:00Z',
  },
  {
    id: 'job_2026_06_09_02',
    entry: 'Imported export — March to June',
    trigger: 'import',
    wrote: 212,
    took: 2_400_000,
    when: '2026-06-09T08:15:00Z',
  },
];

const COLUMNS: ReadonlyArray<Column<DemoRun>> = [
  { key: 'entry', header: 'What was processed', render: (run) => run.entry, primary: true },
  { key: 'trigger', header: 'Triggered by', render: (run) => run.trigger },
  { key: 'wrote', header: 'Records written', render: (run) => run.wrote, align: 'end' },
  { key: 'took', header: 'Took', render: (run) => formatDuration(run.took), align: 'end' },
  { key: 'when', header: 'Started', render: (run) => formatDate(run.when) },
];

const SENTENCES = {
  loading: 'Looking for runs.',
  empty: 'Nothing has been processed yet.',
  filteredEmpty: 'No run matches these filters.',
  failed: 'The runs could not be loaded.',
};

const STATES: ListStatus[] = ['loading', 'empty', 'filtered-empty', 'failed'];

export function KitchenSink() {
  const [overlayOpen, setOverlayOpen] = useState(false);
  const [typed, setTyped] = useState('');

  return (
    <div className="flex flex-col gap-10" data-testid="kitchen-sink">
      <header className="flex flex-col gap-2">
        <h1 className="text-[length:var(--type-page)] leading-[var(--type-page-line)]">
          Kitchen sink
        </h1>
        <p className="text-text-secondary">
          Every part of the interface, in every state. Not part of the product.
        </p>
      </header>

      <Section title="Buttons">
        <Row>
          <Button variant="primary">Primary</Button>
          <Button variant="secondary">Secondary</Button>
          <Button variant="ghost">Ghost</Button>
          <Button variant="secondary" icon={Download}>
            With an icon
          </Button>
        </Row>
        <Row>
          <Button variant="secondary" tone="critical" icon={Trash2}>
            Erase everything
          </Button>
          <Button variant="ghost" tone="critical">
            Erase everything
          </Button>
        </Row>
        <Row>
          <Button variant="primary" disabled>
            Disabled
          </Button>
          <Button variant="secondary" disabled>
            Disabled
          </Button>
          <IconButton icon={Download} label="Download the export" />
        </Row>
      </Section>

      <Section title="Chips">
        <Row>
          <Chip>observation</Chip>
          <Chip tone="positive">settled</Chip>
          <Chip tone="caution">needs attention</Chip>
          <Chip tone="critical">failed</Chip>
          <Chip tone="accent">current</Chip>
          <Chip tone="caution" filled>
            needs you
          </Chip>
        </Row>
      </Section>

      <Section title="Inputs">
        <div className="flex max-w-md flex-col gap-6">
          <Field label="Session label" description="What this conversation should be filed as.">
            {(wiring) => (
              <Input
                {...wiring}
                value={typed}
                onChange={(event) => setTyped(event.target.value)}
                placeholder="evening"
              />
            )}
          </Field>
          <Field label="Required, and unanswered" required error="This cannot be left empty.">
            {(wiring) => <Input {...wiring} />}
          </Field>
          <Field label="How the assistant should behave" description="Replaces the default.">
            {(wiring) => <Textarea {...wiring} placeholder="Write plainly. Do not flatter." />}
          </Field>
          <Field label="Not editable here">
            {(wiring) => <Input {...wiring} disabled value="read from the environment" readOnly />}
          </Field>
        </div>
      </Section>

      <Section title="Record lines">
        <div className="flex flex-col gap-6">
          <RecordLine
            says="I put off the review again and told myself it was timing."
            meta={['observation', formatDate('2026-06-11T00:00:00Z'), 'high', 'active']}
            id="obs_2026_06_11_01_003"
          />
          <RecordLine
            says="Avoids feedback conversations by reframing them as badly timed rather than unwanted."
            meta={['pattern', 'seen 4 times', 'active']}
            id="pat_2026_03_14_01_001"
            trailing={<Chip tone="caution">evolved</Chip>}
          />
          <RecordLine says="A record with nothing else known about it." />
        </div>
      </Section>

      <Section title="Journal text">
        <JournalText>
          {
            'I keep circling the same conversation.\n\nEvery time I decide I will raise it tomorrow, and every time tomorrow turns out to be a bad day for it. I am starting to think there is no good day and that is rather the point.'
          }
        </JournalText>
      </Section>

      <Section title="A table, which is a stack of cards on a phone">
        <DataTable columns={COLUMNS} rows={RUNS} rowKey={(run) => run.id} caption="Recent runs" />
      </Section>

      <Section title="The four ways a list can be empty">
        <div className="flex flex-col gap-4">
          {STATES.map((status) => (
            <div key={status} className="rounded-card border border-border-hairline px-4">
              <StateBoundary
                status={status}
                sentences={SENTENCES}
                failure={
                  status === 'failed'
                    ? new LumenError('unavailable', 'the graph store is not running')
                    : undefined
                }
                onRetry={() => undefined}
                onClearFilters={() => undefined}
              >
                <p>Never seen, because none of these states is ready.</p>
              </StateBoundary>
            </div>
          ))}
        </div>
      </Section>

      <Section title="Saying what is held back">
        <div className="flex flex-col gap-3">
          <Note>Two records were left out of this slice because they are three hops away.</Note>
          <Note tone="caution">
            One record is being held back until you raise the subject yourself.
          </Note>
          <Note tone="critical">
            Extraction failed for this episode: the model returned a type that does not exist.
          </Note>
        </div>
      </Section>

      <Section title="Disclosure and payloads">
        <Disclosure label="What went in" hint="2 messages">
          <PayloadBlock label="stage input">
            {asPayloadText({
              episode_id: 'ep_2026_06_11_01',
              entry_class: 'REFLECTION',
              messages: 2,
            })}
          </PayloadBlock>
        </Disclosure>
        <Disclosure label="Everything it holds" defaultOpen>
          <RecordLine
            says="The review is not about the work, it is about being watched."
            meta={['belief', formatDate('2026-06-11T00:00:00Z'), 'critical']}
            id="bel_2026_06_11_01_002"
          />
        </Disclosure>
      </Section>

      <Section title="Overlay">
        <Row>
          <Button variant="secondary" onClick={() => setOverlayOpen(true)}>
            Open it
          </Button>
        </Row>
        <Overlay
          open={overlayOpen}
          onOpenChange={setOverlayOpen}
          title="A sheet, or a dialog"
          description="Along the bottom on a phone, in the middle on a desktop."
          footer={
            <>
              <Button variant="ghost" onClick={() => setOverlayOpen(false)}>
                Cancel
              </Button>
              <Button variant="primary" onClick={() => setOverlayOpen(false)}>
                Do it
              </Button>
            </>
          }
        >
          <p className="text-text-secondary">
            One component, two layouts, decided in the stylesheet so there is no moment where
            it is the wrong one.
          </p>
        </Overlay>
      </Section>
    </div>
  );
}

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="flex flex-col gap-4">
      <h2 className="text-[length:var(--type-title)] leading-[var(--type-title-line)]">
        {title}
      </h2>
      {children}
    </section>
  );
}

function Row({ children }: { children: ReactNode }) {
  return <div className="flex flex-wrap items-center gap-3">{children}</div>;
}
