# Two-minute demo: click path (synthetic data only)

Nothing here uses real data. "Synthetic demo" is selected in the settings panel (the gear, top
right), and the real-data database is never opened.

## Before every take

```
python -m lookahead.demo reset
```

It rebuilds the synthetic demo database from the persona files: no alerts, planned items,
decisions or corrections, every recurring series waiting to be reviewed. It works while the app is
running. Then press F5 in the browser tab. (It refuses any path in a folder called "private".)

Start the app with `streamlit run app/Home.py` (http://localhost:8501).

## Click path

The top bar has five links (Home, Transactions, Recurring, Forecast, How it works) and the gear.
Transactions, Forecast and How it works have a row of tabs under the bar.

1. **Home.** Status card: data to Oct 1, 2026, account synthetic-A. Click the **gear** (top right),
   set **Account** to **synthetic-C**, close the panel. The card now says synthetic-C, 186
   transactions, 0 of 3 recurring confirmed. (The account choice follows you through the top bar.)
   Below it: **Since last time** ("Nothing to compare yet" after a reset) and **Needs a look**.
2. **Transactions** (top bar), tab **All**. The balance chart, the table with category tags,
   "Details" folded at the bottom. Optional: **Show 50 more**. The tabs **To check** and **Label
   review** sit next to **All**.
3. **Recurring** (top bar). "3 to review". The first card (e-Transfers of one amount, $700 a month)
   is open with its payments; click the **Fizz** card to show the panel follows. Click **Looks
   right, confirm all** (top right): the tags turn Confirmed and the subtitle says "nothing left
   to review".
4. **Forecast** (top bar), tab **Forecast**. The amber callout "Possible shortfall: 97% chance ... around Oct 8".
   Click **See why →**: it lands on **Shortfall alerts** (tagged "provisional, not yet
   validated"). Open **Evidence for this alert**. Scroll to **Projected balance**: low point
   "Low $126.32 · Oct 8".
5. **What if...** (below the chart). Click **Spend less** (defaults to $70 a week): before and
   after in the blue panel (lowest $126.32 to $136.32, chance below buffer 97% to 96%). Scroll to
   **Original and with your change**: grey dash-dot line and band against the green line and blue
   band. For a bigger change on camera, type a larger number in "By".
6. **Scenario** (the tab next to **Forecast**). The calculator: "You'd have $X on Dec 31", the two
   lines "As things are now" and "My scenario", and under the chart "This assumes exactly what you
   entered. It's not a forecast." Raise **Daily spend** to move only the "My scenario" line.
7. Optional: **How it works** (top bar): the tabs **Model report** and **Time machine**.

## Notes

- Names that look like people are shown as "(hidden)" (pseudonymize is on by default); that is
  correct, not broken.
- Every take after the first needs the reset command above, because confirming and dismissing are
  saved in the demo database.
