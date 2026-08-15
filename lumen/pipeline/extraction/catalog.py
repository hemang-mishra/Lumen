"""
The fixed list of things an extraction is allowed to find, and what each
one means.

Extraction is only allowed to label a finding with a name from this list.
Letting a model invent its own labels seems harmless until you look at a
year of them: the same recurring behaviour ends up filed under a dozen
near-identical names, and nothing can be found because nothing was ever
called the same thing twice.

The definitions live here rather than being read from a document at
runtime, so the running system depends on nothing but code. The obvious
risk with that is the two drifting apart, so the list is checked against
the type enum in the tests — a new type without a definition here fails
the suite rather than silently going unexplained to the model.

The list is long on purpose. The rare entries are the valuable ones: a
model that has never been shown "a life-shaped absence" will quietly file
it under "a feeling", and the distinction is the entire reason the type
exists.
"""

from __future__ import annotations

from lumen.schemas.enums import ObservationType

# Types the extraction step is never allowed to produce.
#
# A prosody signal is read from the sound of someone's voice — the pauses,
# the tightening, the break mid-sentence. This stage only ever sees a
# transcript, so there is nothing here to read it from. Asking a model to
# find one anyway would produce a guess dressed up as a measurement.
EXCLUDED_TYPES: frozenset[ObservationType] = frozenset({ObservationType.PROSODY_SIGNAL})

# The only types a thin entry may produce: what happened, and a feeling the
# person actually named. Anything more would be inference, and an entry
# this thin is exactly where inference turns into invention.
RAW_CAPTURE_TYPES: frozenset[ObservationType] = frozenset(
    {ObservationType.CONTEXT, ObservationType.EMOTION}
)


OBSERVATION_TYPE_DEFINITIONS: dict[ObservationType, str] = {
    # --- What happened and how it felt ---
    ObservationType.CONTEXT: (
        "What happened, where, and the circumstances around it."
    ),
    ObservationType.CONTEXT_SEVERANCE: (
        "The shock of abruptly losing access to a place, community or "
        "structure they relied on. Stronger than an ordinary transition."
    ),
    ObservationType.EMOTION: (
        "A feeling as it was experienced at the time."
    ),
    ObservationType.SOMATIC_STATE: (
        "A physical sensation or energy level, such as a heavy body or no "
        "energy to start anything."
    ),
    ObservationType.SOMATIC_CATHARSIS: (
        "A delayed physical and emotional collapse — crying, shaking, "
        "breaking down — rather than a feeling simply described."
    ),
    ObservationType.ANTICIPATORY_ANXIETY: (
        "Fear aimed at something that has not happened yet, projected into "
        "a future or hypothetical situation."
    ),
    ObservationType.COGNITIVE_FRICTION: (
        "Difficulty during mental work, separating plain tiredness from "
        "drifting attention from avoidance of the task."
    ),
    ObservationType.TRIGGER_CATALYST: (
        "The specific thing that set off an emotional or physical state."
    ),
    ObservationType.PROSODY_SIGNAL: (
        "Emotion carried by the sound of the voice rather than the words. "
        "Requires the audio, so it cannot be extracted from a transcript."
    ),
    # --- Coping and response ---
    ObservationType.ENVIRONMENTAL_REANCHORING: (
        "A deliberate act meant to overwrite what a place feels like, such "
        "as cooking a proper meal in a house they were always passive in."
    ),
    ObservationType.COGNITIVE_DEFENSE_MECHANISM: (
        "A mental manoeuvre used to hold a state in place or avoid "
        "discomfort, such as an inner critic switching targets to keep the "
        "pressure on."
    ),
    ObservationType.INTERVENTION_APPLIED: (
        "Something they did to cope or steady themselves, and how well it "
        "worked."
    ),
    ObservationType.ENERGY_SPIKE_EVENT: (
        "A sudden surge of energy or motivation, especially when they "
        "cannot explain where it came from."
    ),
    ObservationType.SUPPRESSED_EMOTION_SURFACING: (
        "A feeling that arrives while they are recording, not while it was "
        "happening, and takes them by surprise. Marks unprocessed depth."
    ),
    # --- How they see themselves ---
    ObservationType.SUBPERSONALITY_ACTION: (
        "Something an inner part of them did, spoken about as its own actor "
        "with its own motives, such as the critic brain."
    ),
    ObservationType.ERA_INTEGRATION_STATE: (
        "How they relate now to a past chapter of their life or an earlier "
        "version of themselves — rejecting it, grieving it, thanking it."
    ),
    ObservationType.RUMINATION_LOOP: (
        "Being stuck replaying the same thoughts involuntarily, as opposed "
        "to deliberately thinking a problem through."
    ),
    ObservationType.PHYSIOLOGICAL_CAPACITY_STATE: (
        "A hard limit on what their body or mind can do, such as only being "
        "able to think properly in the morning. A boundary, not a mood."
    ),
    ObservationType.IDENTITY_AFFINITY: (
        "Something they have realised they are drawn to or suited for."
    ),
    ObservationType.IDENTITY_FUSION_STATE: (
        "Their sense of worth tied to one thing so tightly that losing it "
        "would feel like ceasing to exist — not a preference, a merger."
    ),
    ObservationType.EXISTENTIAL_REFLECTION: (
        "Thinking about meaning, mortality or insignificance as an open "
        "question rather than as present distress."
    ),
    ObservationType.ACCEPTANCE_ACKNOWLEDGEMENT: (
        "Effortfully accepting a hard truth about themselves despite the "
        "discomfort of it."
    ),
    ObservationType.CORE_WOUND: (
        "A root cause reaching back years, named as the origin of a lasting "
        "pattern of pain."
    ),
    ObservationType.SYSTEM_DESIGN_ITERATION: (
        "Building or revising a personal framework or protocol on purpose, "
        "rather than simply behaving a certain way."
    ),
    ObservationType.SELF_NARRATION_PATTERN: (
        "Catching themselves constructing a version of themselves for an "
        "audience — the act of telling the story, not its content."
    ),
    ObservationType.SOCIAL_PERFORMANCE_STATE: (
        "How it feels to be watched: seeing themselves through someone "
        "else's eyes, and the shift from being to being seen."
    ),
    ObservationType.BIOGRAPHICAL_GAP: (
        "Something structurally missing across a whole phase of their life "
        "that most people have, such as never having had a mentor."
    ),
    ObservationType.INAUTHENTICITY_STATE: (
        "Living inside a relationship or group while hiding something "
        "fundamental, where being known carries real risk."
    ),
    # --- How they think and what they believe ---
    ObservationType.EPISTEMIC_SHIFT: (
        "Their understanding of a concept changing, rather than their "
        "behaviour changing."
    ),
    ObservationType.CONCEPTUAL_REFRAME: (
        "Redefining what a word means to them, such as confidence moving "
        "from not being afraid to showing up afraid."
    ),
    ObservationType.LEXICON_UPDATE: (
        "Adopting a new word or distinction to sort experience by, such as "
        "splitting work into thinking and execution."
    ),
    ObservationType.META_BELIEF: (
        "A rule about how they grow or operate, sitting above any single "
        "belief, such as growth requiring self-hatred."
    ),
    ObservationType.COGNITIVE_DISTORTION: (
        "A false belief they caught and corrected, recorded with both the "
        "distortion and the correction."
    ),
    ObservationType.COGNITIVE_DISTORTION_STATE: (
        "A long stretch spent inside a distorted view without noticing, "
        "described only now that it has lifted."
    ),
    ObservationType.METACOGNITIVE_INTERRUPT: (
        "Catching themselves doing the thing as they say it, present tense "
        "and mid-sentence."
    ),
    ObservationType.METACOGNITIVE_BREAKTHROUGH: (
        "A sudden realisation about how their own mind works that changes "
        "the underlying model, not just one behaviour."
    ),
    ObservationType.PERSPECTIVE_SHIFT: (
        "How their view of a past event or person has changed."
    ),
    ObservationType.CORE_CONFLICT: (
        "Two things they want at once that cannot both be had, such as "
        "independence and approval."
    ),
    ObservationType.BELIEF: (
        "A rule about the world or themselves that they operate by."
    ),
    ObservationType.LESSON: (
        "Something learned, grounded in what happened in this episode."
    ),
    # --- Patterns and surroundings ---
    ObservationType.PATTERN: (
        "A recurring behaviour or thought loop. Must be written as the "
        "behaviour, the trigger or context, and the internal state together."
    ),
    ObservationType.OPEN_LOOP: (
        "An unresolved question they are still working through."
    ),
    ObservationType.ENVIRONMENTAL_CONTEXT: (
        "A distinction they draw between places based on what each demands "
        "of them, such as the office being easier than home."
    ),
    ObservationType.ENVIRONMENTAL_DEPENDENCY: (
        "A rule about what a place makes possible or impossible for them — "
        "what it enables, not merely what it was."
    ),
    # --- Other people ---
    ObservationType.OTHER_PERSON_MODEL: (
        "A theory about how another person works, such as their default "
        "language being improvement rather than praise."
    ),
    ObservationType.RELATIONAL_DYNAMIC: (
        "An observation about a relationship itself — its balance, safety "
        "or fairness — rather than a feeling toward the person."
    ),
    ObservationType.GRATITUDE_APPRECIATION: (
        "Explicit thanks for something received, naming what it was and how "
        "deeply it landed."
    ),
}


# The order types are shown to the model in. Grouping helps it choose
# between neighbours that are easy to confuse — a feeling against a
# physical sensation, a caught distortion against one only seen later.
_CATEGORY_ORDER: tuple[tuple[str, tuple[ObservationType, ...]], ...] = (
    (
        "WHAT HAPPENED AND HOW IT FELT",
        (
            ObservationType.CONTEXT,
            ObservationType.CONTEXT_SEVERANCE,
            ObservationType.EMOTION,
            ObservationType.SOMATIC_STATE,
            ObservationType.SOMATIC_CATHARSIS,
            ObservationType.ANTICIPATORY_ANXIETY,
            ObservationType.COGNITIVE_FRICTION,
            ObservationType.TRIGGER_CATALYST,
            ObservationType.PROSODY_SIGNAL,
        ),
    ),
    (
        "COPING AND RESPONSE",
        (
            ObservationType.ENVIRONMENTAL_REANCHORING,
            ObservationType.COGNITIVE_DEFENSE_MECHANISM,
            ObservationType.INTERVENTION_APPLIED,
            ObservationType.ENERGY_SPIKE_EVENT,
            ObservationType.SUPPRESSED_EMOTION_SURFACING,
        ),
    ),
    (
        "HOW THEY SEE THEMSELVES",
        (
            ObservationType.SUBPERSONALITY_ACTION,
            ObservationType.ERA_INTEGRATION_STATE,
            ObservationType.RUMINATION_LOOP,
            ObservationType.PHYSIOLOGICAL_CAPACITY_STATE,
            ObservationType.IDENTITY_AFFINITY,
            ObservationType.IDENTITY_FUSION_STATE,
            ObservationType.EXISTENTIAL_REFLECTION,
            ObservationType.ACCEPTANCE_ACKNOWLEDGEMENT,
            ObservationType.CORE_WOUND,
            ObservationType.SYSTEM_DESIGN_ITERATION,
            ObservationType.SELF_NARRATION_PATTERN,
            ObservationType.SOCIAL_PERFORMANCE_STATE,
            ObservationType.BIOGRAPHICAL_GAP,
            ObservationType.INAUTHENTICITY_STATE,
        ),
    ),
    (
        "HOW THEY THINK AND WHAT THEY BELIEVE",
        (
            ObservationType.EPISTEMIC_SHIFT,
            ObservationType.CONCEPTUAL_REFRAME,
            ObservationType.LEXICON_UPDATE,
            ObservationType.META_BELIEF,
            ObservationType.COGNITIVE_DISTORTION,
            ObservationType.COGNITIVE_DISTORTION_STATE,
            ObservationType.METACOGNITIVE_INTERRUPT,
            ObservationType.METACOGNITIVE_BREAKTHROUGH,
            ObservationType.PERSPECTIVE_SHIFT,
            ObservationType.CORE_CONFLICT,
            ObservationType.BELIEF,
            ObservationType.LESSON,
        ),
    ),
    (
        "PATTERNS AND SURROUNDINGS",
        (
            ObservationType.PATTERN,
            ObservationType.OPEN_LOOP,
            ObservationType.ENVIRONMENTAL_CONTEXT,
            ObservationType.ENVIRONMENTAL_DEPENDENCY,
        ),
    ),
    (
        "OTHER PEOPLE",
        (
            ObservationType.OTHER_PERSON_MODEL,
            ObservationType.RELATIONAL_DYNAMIC,
            ObservationType.GRATITUDE_APPRECIATION,
        ),
    ),
)


def render_type_dictionary(
    *, exclude: frozenset[ObservationType] = EXCLUDED_TYPES
) -> str:
    """
    Write the list of types out for the model to choose from.

    Excluded types are left out entirely rather than listed with a warning
    not to use them. Naming something and forbidding it in the same breath
    invites exactly the behaviour being forbidden; a type the model never
    sees is one it cannot reach for.
    """
    sections = []
    for heading, types in _CATEGORY_ORDER:
        lines = [
            f"  {member.value}: {OBSERVATION_TYPE_DEFINITIONS[member]}"
            for member in types
            if member not in exclude
        ]
        if lines:
            sections.append(f"{heading}\n" + "\n".join(lines))
    return "\n\n".join(sections)


def allowed_types(*, raw_capture: bool) -> frozenset[ObservationType]:
    """
    The types an episode of this kind may produce.

    A full reflection may use anything except what needs audio. A thin
    entry gets two, because a short and unclear entry has nothing in it to
    support a deeper reading.
    """
    if raw_capture:
        return frozenset(RAW_CAPTURE_TYPES)
    return frozenset(set(ObservationType) - EXCLUDED_TYPES)


__all__ = [
    "EXCLUDED_TYPES",
    "RAW_CAPTURE_TYPES",
    "OBSERVATION_TYPE_DEFINITIONS",
    "render_type_dictionary",
    "allowed_types",
]
