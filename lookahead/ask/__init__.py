"""Ask: questions about my money (spec docs/specs/2026-11-20-ask.md).

A question becomes a plan of building blocks (`plan`), written by the offline parser or, when I
turn it on, by an LLM that sees only the question text, the category list and the plan format.
The plan is checked, then run locally (`run`); nothing computed is ever sent anywhere.
"""
