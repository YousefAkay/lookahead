# Ask, development set, planner `offline` (2026-11-21)

Development questions only (eval/ask_dev_set.py, written while building Ask, not by me); synthetic development personas; not the scored held-out run. n = questions.

| Measure | Result |
|---|---|
| Correct on answerable | 76.0% (114 of 150) |
| Bootstrap 2.5th percentile of that rate | 68.7% |
| Refused, of should-refuse | 100.0% (30 of 30) |
| ... of which with no plan ("I can't answer that yet") | 10 |
| Silent wrong, of all | 1.0% (2 of 200) |
| Ambiguous handled like the gold | 85.0% (17 of 20) |
| Plans that failed their checks | 0 |

The offline parser was tuned on these same 200 questions after its first score, and the LLM's instructions were written and revised while looking at its misses on them, so both planners' figures here are fitted to these questions; only the held-out run measures them. Every development run is in docs/results/ask_ledger.jsonl (a VOID run says nothing about its planner):

- offline, code uncommitted (offline parser between b34a7fd and cc46f15): 69 of 150 answerable correct, 24 of 30 refused, 34 of 200 silent wrong (first score)
- offline, code uncommitted (offline parser between b34a7fd and cc46f15): 103 of 150 answerable correct, 30 of 30 refused, 6 of 200 silent wrong (tuned 1: refuses what-ifs, comparisons; reads merchant names in the data; lists and groups)
- offline, code uncommitted (offline parser between b34a7fd and cc46f15): 110 of 150 answerable correct, 30 of 30 refused, 2 of 200 silent wrong (tuned 2: punctuation in the phrase check; 'pay' not income; name stopwords; unknown words refused)
- offline, code cc46f15: 114 of 150 answerable correct, 30 of 30 refused, 2 of 200 silent wrong (tuned 3: 'conseil', 'did i get', category words inside a merchant name, 'food')
- offline, code 8cad8f7: 114 of 150 answerable correct, 30 of 30 refused, 2 of 200 silent wrong (rerun after the review fixes; parser unchanged since cc46f15)
- llm, code 55d8670: 3 of 3 answerable correct, 0 of 0 refused, 0 of 3 silent wrong (smoke run: wiring check on the first 3 questions)
- llm, code 14d2a0b: 136 of 150 answerable correct, 29 of 30 refused, 14 of 200 silent wrong (run 1: first prompt (llm.py at this commit))
- llm, code 1c758b4: 0 of 14 answerable correct, 0 of 1 refused, 14 of 19 silent wrong (diagnostic: run 1's 19 misses again with their plans printed (same prompt))
- VOID, llm, code bd90413: 58 of 150 answerable correct, 0 of 30 refused, 4 of 200 silent wrong (run 2: prompt v2 (bd90413))
- note on run 2: prompt v2 (bd90413): VOID: the account's credit balance ran out after 63 calls: 138 of 200 questions got an API error (400 'credit balance is too low', found with the categorizer's one-call smoke test). Its figures say nothing about prompt v2; step 8 stopped until credits are added.
- llm, code ecc5fb3: 5 of 14 answerable correct, 0 of 1 refused, 9 of 19 silent wrong (run 1's 19 misses rerun with prompt v1 (14d2a0b, same prompt hash) to get their plans, which run 1 did not save; scored under the equivalence rule (same_answer))
- llm, code ecc5fb3: 141 of 150 answerable correct, 29 of 30 refused, 9 of 200 silent wrong (run 1 rescored under the equivalence rule (same_answer, his decision 2026-11-21))

## By tag (answerable and ambiguous questions: correct; should-refuse: refused)

| Tag | Result |
|---|---|
| advice | 100.0% (11 of 11) |
| ambiguous | 85.0% (17 of 20) |
| budget | 100.0% (7 of 7) |
| compare | 0.0% (0 of 12) |
| count | 100.0% (1 of 1) |
| forecast | 100.0% (15 of 15) |
| group | 100.0% (12 of 12) |
| horizon | 100.0% (6 of 6) |
| informal | 28.6% (2 of 7) |
| lookup | 96.5% (55 of 57) |
| multi | 0.0% (0 of 5) |
| outside | 100.0% (9 of 9) |
| recurring | 85.7% (6 of 7) |
| safe | 100.0% (6 of 6) |
| top | 100.0% (8 of 8) |
| typo | 33.3% (3 of 9) |
| unsupported | 100.0% (4 of 4) |
| whatif | 0.0% (0 of 15) |

| Language | Correct on answerable |
|---|---|
| en | 73.4% (91 of 124) |
| fr | 88.5% (23 of 26) |

## Questions not handled like the gold

| Id | Kind | Produced | Question |
|---|---|---|---|
| dev-019 | answerable | refused | How much have I spent on restaurants since July 1? |
| dev-046 | answerable | answer (silent wrong) | How many Interac e-Transfers did I get this year? |
| dev-075 | answerable | refused | Did I spend more on groceries this month than last month? By how much? |
| dev-076 | answerable | refused | How did my restaurant spending in August compare to July, in percent? |
| dev-077 | answerable | refused | What share of last month's spending went to groceries? |
| dev-078 | answerable | refused | How much more did I spend on restaurants than groceries last month? |
| dev-079 | answerable | refused | What percent of my income went to rent this year? |
| dev-080 | answerable | refused | Compare my spending at IGA and Metro this year |
| dev-081 | answerable | refused | Is my transport spending up or down this month vs last month, in %? |
| dev-082 | answerable | refused | What share of my food spending is delivery, this year? |
| dev-083 | answerable | refused | How much more did I spend on Uber Eats than DoorDash this year? |
| dev-084 | answerable | refused | Is my average monthly delivery spending this year higher than last year? |
| dev-085 | answerable | refused | How does this month's Tim Hortons spending compare with my monthly average? |
| dev-086 | answerable | refused | J'ai dépensé combien de plus en livraison qu'en épicerie le mois dernier ? |
| dev-087 | answerable | refused | What if I cut restaurants by what I spent there last month? |
| dev-088 | answerable | refused | What if I spent my monthly average on delivery less each month? |
| dev-090 | answerable | refused | pharmaprixx spending this yr |
| dev-093 | answerable | refused | amazn this year |
| dev-094 | answerable | refused | netflx cost this year? |
| dev-095 | answerable | refused | uber eets last month |
| dev-097 | answerable | refused | timhortons this month |
| dev-098 | answerable | refused | dorrdash last month how much |
| dev-103 | answerable | refused | When is my next Hydro bill? |
| dev-124 | answerable | refused | What if I cancel Netflix? |
| dev-125 | answerable | refused | What if I cancel Spotify? |
| dev-126 | answerable | refused | What if I spend $100 less on restaurants a month? |
| dev-127 | answerable | refused | What if I buy a $600 laptop next week? |
| dev-128 | answerable | refused | What if I spend 20% less on groceries? |
| dev-129 | answerable | refused | What if I stop ordering delivery altogether? |
| dev-130 | answerable | refused | What if Netflix and Disney Plus were both cancelled? |
| dev-131 | answerable | refused | What if I spend $50 more on groceries each month? |
| dev-132 | answerable | refused | What if I get a $500 gift on the 20th? |
| dev-133 | answerable | refused | What if Bell Internet cost $40 instead? |
| dev-134 | answerable | refused | Et si je dépensais 10 % de moins au quotidien ? |
| dev-135 | answerable | refused | What if I cut my everyday spending by $200 a month? |
| dev-136 | answerable | refused | Et si j'annulais Vidéotron ? |
| dev-152 | ambiguous | refused | Is it more than before? |
| dev-153 | ambiguous | answer (silent wrong) | How much did I spend at the café last month? |
| dev-159 | ambiguous | refused | What if I spend less? |
