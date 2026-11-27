# Deploying the public demo (Streamlit Community Cloud)

The public demo is the app in **demo mode**: synthetic personas only, no import, no LLM, no keys,
a "Demo with made-up data" banner on every page, and a guard that refuses to open anything under
`data/private` (DECISIONS.md, 2026-10-11). Demo mode is switched on by one secret,
`LOOKAHEAD_DEMO = "1"`. Nothing else needs a secret.

What Community Cloud uses from the repository (Streamlit's "File organization" and "App
dependencies" pages):

| What | Where | Note |
|---|---|---|
| Entry point (main file path) | `app/Home.py` | the other pages are found in `app/pages/` next to it |
| Dependencies | `requirements.txt` at the repository root | exact pins; installed with pip |
| Python version | chosen in the deploy dialog: **3.13** | the repository is written and tested on 3.13 |
| Config | `.streamlit/config.toml` at the root | the only config file Community Cloud reads; the app runs from the repository root |
| Demo switch | the secret `LOOKAHEAD_DEMO = "1"` | a root-level secret becomes an environment variable when the server starts |
| Time Machine | `data/demo/timemachine/` (committed) | precomputed steps of the seven demo personas, so the server never builds them |

## Before the first deploy

1. The repository must be public on GitHub, with the branch to deploy pushed (`main` once this work
   is merged).
2. Check that `.streamlit/config.toml` has **no** `address` line under `[server]`. It does not on
   this branch: see "Localhost" below for why.

## Click by click

1. Go to **share.streamlit.io** and sign in with GitHub (the first time it asks to connect your
   GitHub account; allow it).
2. In the upper-right corner, click **Create app**.
3. At "Do you already have an app?", click **Yup, I have an app**.
4. Fill in:
   - **Repository:** the public Lookahead repository
   - **Branch:** the branch from step 1 of "Before the first deploy"
   - **Main file path:** `app/Home.py`
   - **App URL** (optional): a subdomain such as `lookahead-demo` (it becomes
     `lookahead-demo.streamlit.app`).
5. Click **Advanced settings**.
6. **Python version:** choose **3.13** (the default is 3.12; if 3.13 is not offered, 3.12 should
   work too, but it is the version the tests were not run on).
7. **Secrets:** paste exactly this one line:

   ```toml
   LOOKAHEAD_DEMO = "1"
   ```

8. Click **Save**, then **Deploy**.
9. Wait for the build (a few minutes: scikit-learn and pandas are large). The log is on the right.
10. When it opens, check by eye (below). The first visit builds the synthetic demo database and
    trains the demo categorizer (the page says "Building the demo, about a minute"); later visits
    are quick.
11. Run the smoke test against the link (below).

## Check by eye on the deployed app

- Home starts with the **MADE-UP DATA** box and opens on **synthetic-C**.

- Every page shows the amber **Demo with made-up data** banner under the green bar.
- Settings (top right) offers **Synthetic demo** only; there is no "My data".
- **Forecast** for synthetic-B: the **Safe to spend** card says "Plenty of room" with the exact
  figure in small print; synthetic-A says "No safe amount: your balance is already below your $200
  buffer."
- **Time machine** opens on a persona with dates to step through (from the committed snapshots).
- **To check:** the LLM fallback says it is not offered.
- **Model report:** the real column says to run it locally.

## Smoke test

`tools/demo_smoke.py` opens the demo in a real browser (Playwright) and checks every page; see its
docstring for the list. Once per machine:

```
pip install -r requirements-dev.txt
python -m playwright install chromium
```

Before a deploy (it starts a local demo on a fresh temporary database):
`python -m tools.demo_smoke`. After a deploy: `python -m tools.demo_smoke --url <the link>`; its
first line gives the time the first page took, which after a sleep is the wake time. It stops at
once if the first page has no "Demo with made-up data" banner. `--screenshots docs/img` also saves
the three README pictures.

On Community Cloud the app runs in a frame inside Cloud's own page, and Cloud adds its toolbar
(Fork and GitHub for a visitor; Share, star, edit and GitHub for the owner) to the top right of
the app. So the green bar keeps room for that toolbar, and the app's own links are relative to
the current page: a link from the site root loads Cloud's page a second time inside the frame,
and the frame may not open links in the top window. The smoke test looks in the frame and
checks that every top-bar click opens its page.

## Localhost: why the config file has no `address` line

Locally, with my own data, the app must be reachable from this computer only. That used to be
`address = "localhost"` in `.streamlit/config.toml`. Community Cloud reads that same single file
("you can only have one configuration file", and it must be at the root), and the options it
overrides (`showErrorDetails`, `fastReruns`, `runOnSave`, `enableXsrfProtection`,
`gatherUsageStats`) do **not** include `server.address`. So the line would also bind the public
demo to localhost, and whether Community Cloud's proxy can still reach it is not documented.

So the rule moved into code. Every page checks the address it is served on (`apply` in
`app/theme.py`): outside demo mode, anything but `localhost`, `127.0.0.1` or `::1` shows how to
start the app locally and stops before any data is read. Demo mode may be served on any address.
Locally I start the app with:

```powershell
streamlit run app/Home.py --server.address localhost
```

or once put this in `~/.streamlit/config.toml` (my own machine, not the repository):

```toml
[server]
address = "localhost"
```

**Fallback if the deployed app still does not come up** (the log shows the server started, but the
page never loads): it is not the address binding (the repository no longer sets one). Open
**Manage app** (bottom right of the app) → **⋮** → **Reboot app**; if the log shows an install
error, check that the Python version is 3.13 in **Settings** → **General**; if it shows "demo mode"
is off (a page with a red "runs on this computer only" message), the secret is missing: **Settings
→ Secrets**, add `LOOKAHEAD_DEMO = "1"`, **Save**, and reboot.

## Run demo mode locally

```powershell
$env:LOOKAHEAD_DEMO = "1"
streamlit run app/Home.py
Remove-Item Env:LOOKAHEAD_DEMO   # back to normal afterwards
```

(macOS or Linux: `LOOKAHEAD_DEMO=1 streamlit run app/Home.py`.) In demo mode no `--server.address`
is needed, `.env` is never read and nothing under `data/private` can be opened.

## Known limits of the free demo

- Each visitor works on their own copy of the demo database (`lookahead/demo.py`). The base is
  built once per server start and never written after that. The copy's id travels in the address
  (`?visit=...`), because every top-bar link starts a new browser session; so **sharing a link
  shares that copy**. "Start the demo again" (under the gear) gives a fresh one. A copy nobody
  used for 6 hours is deleted, and a reboot (usually a sleep too) deletes them all.
- An app with no visitors goes to sleep after a while; the next visit wakes it (a minute).
- To refresh the Time Machine snapshots after a forecaster change:
  `python -m lookahead.timemachine_snapshots --out data/demo/timemachine` (synthetic personas only,
  about six minutes), then commit the folder.
