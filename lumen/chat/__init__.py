"""
Talking to Lumen from a terminal.

The whole conversational layer is invisible by design — it happens between
somebody speaking and the assistant answering, and none of it reaches a
screen. That is right for the product and leaves nobody able to judge whether
it works.

So this prints both halves: the reply as it is written, and underneath it
what was actually decided and fetched to write it. It exists to be read by a
person deciding whether the thing is any good, which is the one judgement no
test can make.
"""

from lumen.chat.session import ChatRunner, build_runner

__all__ = ["ChatRunner", "build_runner"]
