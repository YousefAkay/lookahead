# Ask held-out set: fingerprints

The held-out folder (spec `docs/specs/2026-11-20-ask.md`, step 9) is git-ignored and blocked. Its files are committed only after the one scored run; these SHA-256 fingerprints are committed before any scoring, so the files used later can be checked against them (the module's `fingerprint` command).

## 2026-11-21: questions and both gold sets, before the review

100 questions by two independent question-writer agents (50 `ha-`, 50 `hb-`); two independent gold-plan sets (A and B), each with 100 plans and `check-gold` reporting no problems. Each set by expected outcome: answer 75, clarify 10, not found 1, refused 14 (n = 100).

| File | Lines | SHA-256 |
|---|---|---|
| questions_agent.jsonl | 50 | 0854b44fb19390bf3b26eb13967bbbeb58b0fce29a5c92ce5727ac5186a459a2 |
| questions_agent_b.jsonl | 50 | 18af90ad8fad5cda0dbbae3995403e7d75f97d9e87f399281fbabe86f3d55f0c |
| gold_a.jsonl | 100 | ca3c9ade3c4ec24d058df984b99ce3615c63475648e8efe87f954cf805ce61e2 |
| gold_b.jsonl | 100 | 7c8e465799467ccfca570934b2e59589062914b38ea890271e0b48c88fb090cb |
| review_cases.json, answers.txt, gold_fixes.jsonl, gold_final.jsonl | absent | |

Set fingerprint: `8436b1faf1bccec7c0930f369998782564e645e7e426f5affe999d9f05215a9a`

The review files, any fixes and the final gold are added after the owner's review, and their fingerprint is committed here before the scored run.
