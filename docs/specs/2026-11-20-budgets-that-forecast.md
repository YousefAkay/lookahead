# Budgets that forecast, spec (2026-11-20)

## What it is
Monthly spending limits for a few discretionary categories, proposed from my own history and accepted with one "Looks right" button. For each budget I see what I have spent so far this month, the limit, the likely end of the month, and the chance of going over, all computed from the same 500 simulated futures as the forecast. I also see what staying on budget would do to my lowest balance, and I get an alert when a budget is more likely than not to be exceeded. It is a plain product feature: every figure is labelled an "estimate", and it makes no accuracy claim.

## Decisions
- How limits are proposed: the median of the last 3 full calendar months for that category, rounded up to the next $5. With fewer than 2 full months of history in a category, there is no proposal; I see an "enter a limit" box instead. (The median ignores one unusual month, and 3 months keeps it recent.)
- Accepting: one "Looks right" button accepts all proposals, in the same style as the recurring proposals. I can edit any limit afterwards, and a change applies to the current month at once.
- What "month" means: the calendar month. (I have no paycheque to anchor a pay cycle.)
- Which month is "this month": the month of the forecast's first day, which is the day after my data ends, with the "Data to ..." chip shown. (So the budget figures agree with the forecast and safe-to-spend.)
- Stale data: when that budget month is not today's calendar month, the Budgets tab shows one line saying my data ends earlier and to import to see this month. (Added 2026-11-20.)
- Test data: every synthetic test uses development personas only; held-out personas are never used. (Added 2026-11-20.)
- Pace and the chance of going over: spent so far this month (rows posted on or before the data date) plus, in each of the 500 futures, that category's spending on the days left in the month. The chance of going over is the share of futures that end the month above the limit. "Likely end of month" is the middle future. (It uses the same draws as the forecast, so the two agree.)
- Rule zero: each future draws whole past weeks from the 13 weeks up to the data date, exactly as the forecast does. A drawn week now also carries how its spending splits across categories, and nothing posted after the data date is used.
- Partial weeks: a drawn week is cut by day, never scaled. The cut uses the forecaster's existing day mapping, including its weekday alignment (a drawn week starts on the same weekday as the forecast week), not a new one: each forecast day takes the spending of its own day in the drawn week (the forecast's first day takes day 1 of the first drawn week, and so on), and the days left in the month are simply the forecast days up to the month's last day. With 3 days left, the rest of the month is the first 3 days of the first drawn week, exactly. Tested at 1, 3 and 6 days left: the remaining spending equals the sum of those first days of the drawn week, to the cent.
- Big one-off rows (at least $500 and at least 5 times the typical row, the forecaster's existing rule) are left out of the futures but count in "spent so far". When this month's total in a category includes one, the budget view shows a short note saying so.
- Recurring payments (placed by the forecaster on their own dates) are not part of a category's futures.
- Effect of staying on budget: a what-if that, in each future, caps each budgeted category's spending for the rest of the month at the limit minus spent so far (no cut where the future is already under it). It shows my lowest balance (middle future) and the chance of dipping below my buffer, as things are now beside on budget, compared draw for draw.
- Budget alerts: an alert when the chance of going over is above 50%, from day 8 of the month onward. There is one episode per category per month, and it ends at month end. A dismissed episode comes back only when the chance enters a higher band (20, 50 or 80%). The likely overrun moving by $25 only refreshes the evidence of an open episode; it never brings a dismissed one back. Bring back works as for the shortfall alert. Evidence shows spent so far, the limit, the likely end of month, and the rows behind spent so far.
- Where it lives: a "Budgets" tab in the Forecast section. Open budget alerts also appear in "Needs a look" on Home.
- Categories: on by default are Food delivery, Restaurants & cafés, Entertainment and Shopping. Optional are Groceries, Transport, Personal care, Health & pharmacy, Gifts & donations and Cash. Income, Transfers, Rent & housing, Subscriptions, Phone/internet/utilities, Fees & interest and Education can never have a budget.
- Recategorize: under each bar is a list of this month's rows in that category. Each row has a category menu with "just this row" or "all rows of this merchant", using the existing corrections. The pace, chance and chart update at once.
- Chart: one bar chart per category, showing spent so far, the limit, and the likely end of month.
- Safe-to-spend: independent of budgets. The Budgets tab shows the current safe-to-spend figure as one line for context, and its method does not change.
- Demo mode: the demo persona's proposals are shown and "Looks right" works, but accepting lasts only for the session and is never written to the committed demo snapshots.
- After my real-data check (added 2026-11-20): once spent so far is above the limit, the chance of going over is 100% (a refund drawn from a past week does not undo money already spent). Before Looks right, each card's limit box holds the proposal, and a $0 limit is refused with a message. The four figures are in two rows of two, the chance also sits beside the category name, and the chart has three labelled bars (spent so far, limit, likely end of month) plus the limit line. The check's rows column is named "rows this month" and the check counts the corrections I stored.
- Series categories win (added 2026-11-20): a row whose category comes from a recurring series I gave a category is not moved from the budget view; the page says so and points to the Recurring page. When spent so far is already above the limit, the card and alert say "You're already over this budget".
- CV: no claim. If I ever want to claim the chance of going over is reliable, that needs its own pre-declared adopt rule on development personas (a score of the month-end overrun chance at fixed days of the month, against a run-rate baseline, with n as persona-months).

## Out of scope
- Rolling unused budget over to the next month.
- Weekly budgets, budgets on income, envelope or zero-based budgeting.
- Any change to the safe-to-spend method or to the forecast itself.
- Any accuracy claim for the chance of going over.

## Definition of done
1. With at least 2 full months of history, each default category shows a proposed limit equal to the median of its last 3 full months (or of the 2 there are), rounded up to the next $5. With fewer than 2, there is an "enter a limit" box. Tested on synthetic data, including a category with zero spending in a month.
2. "Looks right" accepts all proposals in one click. Limits are editable afterwards and saved, and optional categories can be added and removed. The never-budget categories cannot be chosen.
3. Each budget shows spent so far, the limit, likely end of month and the chance of going over, labelled "estimate", in one bar chart per category.
4. A test shows the chance and the likely end use only rows posted on or before the data date: adding rows after the data date changes nothing.
5. Partial-week tests at 1, 3 and 6 days left in the month pass: the rest of the month is cut by day from the drawn week, not scaled. A test shows that in every future, the sum over all categories of each day's spending equals the forecast's everyday spending for that day, to the cent.
6. A big one-off row this month counts in spent so far, is absent from the futures, and triggers the note in the budget view (tested).
7. The on-budget what-if never gives a lower balance than "as things are now" in any future, on any day (tested), and is shown beside it.
8. Budget alerts open above 50% from day 8, one episode per category per month, end at month end, and a dismissed one comes back only when the chance enters a higher band, never on a $25 overrun move alone (tested on a replay of synthetic data dates). Open alerts appear in "Needs a look" on Home.
8b. The stale-data line shows when the budget month is not today's calendar month, and not otherwise (tested).
9. Recategorizing one row, or a whole merchant, from the budget view moves it out of that category's spent so far at once.
10. Safe-to-spend gives byte-identical output before and after the change on synthetic origins, and the forecast's own output is unchanged.
11. Demo mode shows proposals; accepting works in the session and leaves the committed demo snapshots unchanged (tested).
12. A counts-only check command exists for my real data. The quick test subset and the full suite pass, and the reviewer gives PASS before the merge.

## My real-data checks
1. Run the counts-only budget check on my database: it prints the number of categories proposed, the months used per proposal (counts), the number of categories with a chance of going over, the number of open budget alert episodes, and yes/no for "on budget lowest balance is at or above as-now lowest balance in every future". I report the counts and the yes/no only.
2. In the browser, fine or broken for each: the Budgets tab opens; "Looks right" accepts the proposals; editing a limit sticks after a reload; recategorizing one row moves it out of the category; the big one-off note appears when it should (or I say there was none this month); dismissing a budget alert works and bring back works.

## Open points
None.
