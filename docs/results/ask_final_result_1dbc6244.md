# Ask, the scored run on the test set (2026-11-26)

**FAIL** under the committed rule. n = 100 questions on synthetic development personas, written by two question-writer agents; both gold keys are model-written.

| Measure | LLM planner | Offline parser |
|---|---|---|
| Correct on answerable | 93.4% (71 of 76) | 43.4% (33 of 76) |
| Bootstrap 2.5th percentile of that rate | 86.8% | 31.6% |
| Refused, of should-refuse | 92.9% (13 of 14) | 85.7% (12 of 14) |
| Silent wrong, of all | 8.0% (8 of 100) | 10.0% (10 of 100) |
| Ambiguous handled like the gold | 80.0% (8 of 10) | 20.0% (2 of 10) |
| Plans that failed their checks | 0 | 0 |

| Pass condition (LLM planner) | Met |
|---|---|
| 1. correct on answerable >= 85% | yes |
| 2. bootstrap 2.5th percentile of that rate >= 75% | yes |
| 3. refuses >= 90% of should-refuse questions | yes |
| 4a. silent wrong <= 5% of all questions | no |
| 4b. beats the offline parser on correct-on-answerable by >= 20 points | yes |

| Correct on answerable, by | LLM planner | Offline parser |
|---|---|---|
| language en | 92.4% (61 of 66) | 42.4% (28 of 66) |
| language fr | 100.0% (10 of 10) | 50.0% (5 of 10) |
| writer ha | 92.1% (35 of 38) | 47.4% (18 of 38) |
| writer hb | 94.7% (36 of 38) | 39.5% (15 of 38) |

Model claude-haiku-4-5-20251001, temperature 0, one run, code cb6f161. Spent 0.3540 USD on 101 calls; API failures 0 (each counted as not correct). Set fingerprint `9fb38ba735b7c61ddca216f4b2013e12656d15477009492a8a768228a95bd1bb`.

Earlier void attempts (nothing of theirs was scored here): none.

## By question

| Id | Kind | LLM | LLM as the gold | Offline | Offline as the gold |
|---|---|---|---|---|---|
| ha-001 | answerable | answer | yes | answer | yes |
| ha-002 | answerable | answer | yes | refused | no |
| ha-003 | answerable | answer | yes | answer | yes |
| ha-004 | answerable | answer | yes | answer | yes |
| ha-005 | answerable | answer | yes | answer | yes |
| ha-006 | answerable | answer | yes | answer | yes |
| ha-007 | answerable | answer | no | refused | no |
| ha-008 | answerable | answer | yes | answer | yes |
| ha-009 | answerable | answer | yes | refused | no |
| ha-010 | answerable | answer | yes | answer | no |
| ha-011 | answerable | answer | yes | refused | no |
| ha-012 | answerable | answer | yes | answer | yes |
| ha-013 | answerable | answer | yes | refused | no |
| ha-014 | answerable | answer | no | answer | yes |
| ha-015 | answerable | answer | yes | refused | no |
| ha-016 | answerable | answer | yes | refused | no |
| ha-017 | answerable | answer | yes | answer | yes |
| ha-018 | answerable | answer | yes | answer | yes |
| ha-019 | answerable | answer | no | refused | no |
| ha-020 | answerable | answer | yes | not_found | no |
| ha-021 | answerable | answer | yes | answer | yes |
| ha-022 | answerable | answer | yes | answer | yes |
| ha-023 | answerable | answer | yes | not_found | no |
| ha-024 | answerable | answer | yes | refused | no |
| ha-025 | answerable | answer | yes | refused | no |
| ha-026 | answerable | answer | yes | answer | yes |
| ha-027 | answerable | answer | yes | answer | yes |
| ha-028 | answerable | answer | yes | refused | no |
| ha-029 | answerable | answer | yes | answer | no |
| ha-030 | answerable | answer | yes | answer | no |
| ha-031 | answerable | answer | yes | refused | no |
| ha-032 | answerable | answer | yes | refused | no |
| ha-033 | answerable | answer | yes | refused | no |
| ha-034 | answerable | answer | yes | answer | yes |
| ha-035 | answerable | answer | yes | answer | yes |
| ha-036 | answerable | answer | yes | answer | yes |
| ha-037 | answerable | answer | yes | answer | no |
| ha-038 | answerable | answer | yes | answer | yes |
| ha-039 | ambiguous | clarify | yes | refused | no |
| ha-040 | ambiguous | clarify | yes | clarify | yes |
| ha-041 | ambiguous | clarify | yes | refused | no |
| ha-042 | ambiguous | clarify | yes | not_found | no |
| ha-043 | ambiguous | clarify | yes | refused | no |
| ha-044 | should_refuse | refused | yes | refused | yes |
| ha-045 | should_refuse | refused | yes | refused | yes |
| ha-046 | should_refuse | refused | yes | refused | yes |
| ha-047 | should_refuse | refused | yes | refused | yes |
| ha-048 | should_refuse | refused | yes | not_found | no |
| ha-049 | should_refuse | refused | yes | refused | yes |
| ha-050 | should_refuse | refused | yes | refused | yes |
| hb-001 | answerable | answer | yes | answer | yes |
| hb-002 | answerable | answer | yes | answer | yes |
| hb-003 | answerable | answer | yes | answer | yes |
| hb-004 | answerable | answer | no | refused | no |
| hb-005 | should_refuse | refused | yes | refused | yes |
| hb-006 | answerable | answer | yes | answer | yes |
| hb-007 | ambiguous | clarify | yes | refused | no |
| hb-008 | answerable | answer | yes | answer | yes |
| hb-009 | answerable | answer | yes | answer | yes |
| hb-010 | answerable | answer | yes | refused | no |
| hb-011 | answerable | answer | yes | refused | no |
| hb-012 | answerable | answer | yes | refused | no |
| hb-013 | answerable | answer | yes | refused | no |
| hb-014 | should_refuse | refused | yes | refused | yes |
| hb-015 | answerable | answer | yes | answer | no |
| hb-016 | answerable | answer | yes | answer | yes |
| hb-017 | answerable | answer | yes | refused | no |
| hb-018 | ambiguous | answer | no | answer | no |
| hb-019 | answerable | answer | yes | refused | no |
| hb-020 | answerable | answer | yes | refused | no |
| hb-021 | answerable | answer | yes | answer | yes |
| hb-022 | answerable | answer | yes | refused | no |
| hb-023 | answerable | answer | yes | refused | no |
| hb-024 | should_refuse | refused | yes | refused | yes |
| hb-025 | answerable | answer | yes | refused | no |
| hb-026 | answerable | answer | yes | refused | no |
| hb-027 | ambiguous | clarify | yes | clarify | yes |
| hb-028 | answerable | answer | yes | answer | no |
| hb-029 | answerable | answer | no | answer | yes |
| hb-030 | answerable | answer | yes | answer | no |
| hb-031 | answerable | answer | yes | refused | no |
| hb-032 | answerable | answer | yes | answer | yes |
| hb-033 | answerable | answer | yes | answer | yes |
| hb-034 | answerable | not_found | yes | refused | no |
| hb-035 | should_refuse | answer | no | refused | yes |
| hb-036 | answerable | answer | yes | answer | yes |
| hb-037 | answerable | answer | yes | answer | no |
| hb-038 | answerable | answer | yes | answer | no |
| hb-039 | answerable | answer | yes | answer | yes |
| hb-040 | ambiguous | clarify | yes | refused | no |
| hb-041 | answerable | answer | yes | refused | no |
| hb-042 | answerable | answer | yes | refused | no |
| hb-043 | should_refuse | refused | yes | not_found | no |
| hb-044 | ambiguous | answer | no | not_found | no |
| hb-045 | answerable | answer | yes | answer | yes |
| hb-046 | answerable | answer | yes | refused | no |
| hb-047 | answerable | answer | yes | refused | no |
| hb-048 | should_refuse | refused | yes | refused | yes |
| hb-049 | should_refuse | refused | yes | refused | yes |
| hb-050 | answerable | answer | yes | answer | yes |
