"""
Turning what was fetched into what the assistant reads.

One name comes out of here. The allowances, the wording of each kind of
record, the rules about repetition and the block itself are how the briefing
is built, not choices a caller makes.
"""

from lumen.query.assembly.stage import ContextAssembler

__all__ = ["ContextAssembler"]
