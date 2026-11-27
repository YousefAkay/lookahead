# Budgets N5 and the hosted demo (week 8, item 4)

Agreed 2026-11-27. What, not how.

## Decisions

1. Host on Streamlit Community Cloud, as in docs/DEPLOY.md.
2. N5: every visitor gets their own copy of the demo database. Tests: a change in one visit is not
   seen in another, the base stays unchanged, a copy is never inside a private folder, unused
   copies are cleaned up.
3. The base is built when the server wakes, with a clear message ("Building the demo, about a
   minute") instead of a blank page. After going live the real first-visit time after a sleep is
   measured and reported; committing a prebuilt database is revisited only if it is well over a
   minute.
4. The demo opens on synthetic-C (a fresh low-balance warning with its evidence).
5. Home keeps its layout plus a three-line intro box in demo mode; "made-up data" impossible to
   miss.
6. A "Start the demo again" button.
7. A committed browser smoke test, `tools/demo_smoke.py`, with Playwright pinned in
   requirements-dev.txt. It checks the Scenario chart, the Recurring "Next <date>" wrap and the
   console errors, and runs only against the synthetic demo.
8. Three README pictures: Forecast with its warning, Ask answering a question, Scenario. Not
   Budgets: the README leads with forecast, alerts and Ask. Ask is checked in demo mode (offline
   parser, LLM off).

## Done when

- The smoke test passes locally, and on the live link after the deploy.
- Two browser windows do not see each other's changes.
- The README shows the three pictures and, after the deploy, the link.
- Scrub scan before the pictures are committed; full merge gate and a reviewer PASS.
- Deploy clicks are the owner's; the link, the smoke run on it and the wake time follow.

Out of scope: upload, the LLM, real data in any form, Budgets in the README.
