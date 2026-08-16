"""
Rules every prompt in the system carries, whatever stage it belongs to.

Each stage writes its own instructions, deliberately — what extraction is
asked for has nothing to do with what reconciliation is asked for. A handful
of rules are not like that: they hold everywhere, and a rule restated in five
files is a rule that will eventually disagree with itself in one of them.

Anything here has to be worth that. The test is whether a stage could
reasonably want to do it differently; if it could, the rule belongs in that
stage's own prompts file instead.
"""

from __future__ import annotations

# How the person whose history this is gets referred to.
#
# Their name is in their own writing, and models misspell it — not once, but
# in whichever observation, summary or digest happens to mention it. Those
# strings are the permanent record, so a misspelling is not a cosmetic
# problem: it is somebody's name written wrongly in their own history, in a
# place they will read it back.
#
# The name is not needed for anything. There is one person per deployment,
# every record is already theirs, and nothing is retrieved by their name.
# Other people are a different matter — who they are is exactly what makes a
# record about them useful — so this narrows to the author alone.
AUTHOR_NAMING = (
    "Always refer to the person whose journal this is as \"User\", never by "
    "name, even where they sign their writing or the assistant addresses them "
    "by it — models misspell it and the misspelling would be written into "
    "their permanent record. Other people keep their names exactly as written."
)


__all__ = ["AUTHOR_NAMING"]
