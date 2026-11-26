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

## 2026-11-25: after the owner's review, before the scored run

The owner answered all 10 review cases (`apply-review`: 10 of 10, OK 10). No case was marked X, so no fixer ran and `gold_fixes.jsonl` does not exist. `gold_final.jsonl` holds 100 plans for 100 questions; `check-final`: answer 75, clarify 10, not found 1, refused 14, problems none (n = 100). The questions and both gold sets are unchanged from the 2026-11-21 fingerprints above.

| File | Lines | SHA-256 |
|---|---|---|
| questions_agent.jsonl | 50 | 0854b44fb19390bf3b26eb13967bbbeb58b0fce29a5c92ce5727ac5186a459a2 |
| questions_agent_b.jsonl | 50 | 18af90ad8fad5cda0dbbae3995403e7d75f97d9e87f399281fbabe86f3d55f0c |
| gold_a.jsonl | 100 | ca3c9ade3c4ec24d058df984b99ce3615c63475648e8efe87f954cf805ce61e2 |
| gold_b.jsonl | 100 | 7c8e465799467ccfca570934b2e59589062914b38ea890271e0b48c88fb090cb |
| review_cases.json | 52 | f19a043fd702c509cc649547411da2407a8c0f4d324e299fc33d229d7c1dd305 |
| answers.txt | 10 | e9142b16763b9f5bf27c9fd93a4aa21f305e714bf185817a73cf9467aaa8393d |
| gold_fixes.jsonl | absent | |
| gold_final.jsonl | 100 | 95c65c2f69916b1397f2c77dbd93895c3f86f88d15f51d73e753440210a7955a |

Set fingerprint: `9fb38ba735b7c61ddca216f4b2013e12656d15477009492a8a768228a95bd1bb`
