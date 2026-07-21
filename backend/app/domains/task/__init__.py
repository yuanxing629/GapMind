"""Task runtime domain.

Manages long-running async tasks (PDF parse, embed, extract, discover).
State machine: Queued -> Running -> WaitingForUser | Succeeded | Failed |
Cancelled.
"""
