# Spec: history scrub scan (2026-11-17)

## What

A read-only scanner, `tools/scrub_scan.py`, that lists what must be cleaned before the repository
goes public. It covers every commit reachable from every branch (local and remote-tracking) and
every tag, including local-only tags such as `archive/lightgbm-prototype`: not just the files at
one branch's tip.

The report says counts, file paths and commit ids only. It never quotes an amount, a merchant
line or any other matched text from the files or my patterns. The one exception: for a commit
message it names which words of the script's own public AI-tool list matched.

## Decisions (Yousef, 2026-11-17)

- The report is local only: written to the git-ignored `logs/` folder, never committed.
- Author and committer identities are in scope: commits counted per identity, emails masked
  (first letter and domain only). *(Superseded in the second round: my gmail address stays and
  is shown; the others are flagged.)* The history rewrite will switch them to the GitHub noreply
  address.
- Brand names in the committed synthetic persona files (`data/synthetic/persona_*`) are counted
  in their own section, apart from real-merchant hits elsewhere.
- The script is committed as `tools/scrub_scan.py`: patterns only, no data.
- The old committed report `docs/prepublic-scrub-report.md` quotes amounts. The scrub removes it
  from the whole history (not just edits it), and the new report lists every commit and ref that
  holds it.

## Added 2026-11-17 (Yousef, before the build)

- **No personal patterns in the committed script.** `tools/scrub_scan.py` holds only generic
  rules: the AI-tool word list, a generic amount rule, the brand names the synthetic generator
  already uses, and a generic "my own account" wording rule. My name, my amounts, e-Transfer
  names, my merchants and exact wordings live in a git-ignored file I fill in myself,
  `data/private/scrub_patterns.txt`, passed with `--patterns`. The script never opens it unless
  that option is given, so a run without `--patterns` (generic rules only) never touches it;
  the run with my patterns is mine. Its format (one pattern a line, matched without case):

  ```
  # a line starting with # is ignored
  name: <a name, e.g. my first and last name>
  amount: <an amount as it appears, e.g. 1234.56 or $1,234.56: matched by value>
  etransfer: <a name on an e-Transfer>
  merchant: <a merchant I use>
  phrase: <any exact wording to find, e.g. a sentence about my income timing>
  ```

  My patterns are also matched against commit messages, and an amount is also matched when it
  is written as whole cents. The report gives counts per category only, never the pattern or the
  matched text. The tests
  use a made-up patterns file.
- **Email** *(superseded in the second round: one public gmail address, no noreply)*. After the
  rewrite, every commit (old and new) uses my GitHub noreply address; new
  commits get it from `git config`, and I check GitHub's email privacy settings.
- **Coverage, confirmed:**
  - commit messages: the AI-tool word list includes Claude, Anthropic, Co-Authored-By and
    "Generated with" (plus the old report's list);
  - DECISIONS lines with amounts: the generic amount rule and my `amount:` patterns, in every
    version of every file;
  - the income-timing wording: the generic "my own account" wording rule (lines that speak of my
    account, my data, my income, my pay, my rent and the like) and my `phrase:` patterns;
  - the old scrub report: listed with every commit and ref that holds it;
  - cached commits of deleted branches (such as the income prototype): the scan reads every
    commit in this clone, including those only in a reflog and unreachable ones, and says which
    kind each finding is in. *(Superseded in the second round: there is no force-push. The
    rewrite goes to a new repository and the old one stays private as `lookahead-archive`, so no
    purge request is needed.)* GitHub may still serve old pushed commits by their id after the
    force-push; the scrub plan includes asking GitHub to purge them.

## Added 2026-11-17, second round (Yousef)

- **Patterns built from my bank files.** I can't hand-list every e-Transfer name, so
  `tools/scrub_patterns.py`, run by me, builds the patterns from my exports and my database:
  every e-Transfer counterpart name (the known businesses of my allowlist left out), every
  transaction amount over $100, every run of 7 or more digits in the exports and their file names
  plus every mobile-banking reference, and my card merchants. It adds the few lines I type myself
  (`--extra`: name spellings, address, building; categories `name`, `address`, `phrase`), runs
  the scan, writes the report where I say, and prints only how many patterns it built per
  category, the section headings and the section 7 counts. Every path is given on its command
  line; there is no default. Tests use made-up bank files. *(Superseded in the third round: see
  below.)*
- **Text patterns match whole words**, without case; a bare number (no `$`, no cents) that equals
  one of my amounts, as whole cents or whole dollars, is counted apart from formatted amounts.
- **Email.** My gmail address stays (it is public). `--public-email` shows it as it is; any other
  address is masked and marked "to rewrite". No noreply address.
- **Brand names** outside the synthetic persona files are split into those that are also one of
  my own card merchants and the rest (only with my patterns).
- **Section 9:** file lines that name an AI tool or assistant (many are the app's optional LLM
  fallback, which stays).
- **The scrub plan** is in DECISIONS (2026-11-17): rewrite on a fresh clone, push to a new empty
  repository (only after I approve creating it), rename the old one `lookahead-archive` and keep
  it private.

## Added 2026-11-17, third round (Yousef, after the review)

- **`--mine` is required.** A bare run of the builder reads nothing and stops with a message.
  With `--mine`, any path I leave out defaults to my private folder (found from any worktree);
  a default file that does not exist is skipped by name, and a path I type that does not exist
  stops the run.
- **Whole files that leave the history** are `leave:` lines of my private extras file (one
  repository path each). The public code names only the old scrub report.

## Sections of the report

1. Commit messages that mention an AI tool: commit ids, the matched words of the public list,
   how each commit is reached, and which refs contain it.
2. Author and committer identities: commits per masked identity.
3. Lines that look like a personal dollar amount: per path, how many distinct lines, in how many
   commits, and whether the path still has them at the tip of `prepublic-prep` or only in history.
4. Lines that speak of my own account (my account, my income, my pay and the like): the same
   counts.
5. Brand names outside the synthetic persona files: the same counts.
6. Brand names in the synthetic persona files, separately.
7. My personal patterns (only with `--patterns`): counts per category, per path, and the commit
   messages that match (commit ids and categories only).
8. Files that must leave the history whole: the old scrub report; the `leave:` paths of my
   patterns file (only with `--patterns`); anything under
   `data/private`, any `.env`-style file or folder (not `.env.example`), `DECISIONS-private.md`,
   or a database outside `data/demo/timemachine` (private names counted only; contents never
   read).
9. File lines that name an AI tool or assistant: the same counts as 3.

## What it never does

- Opens the contents of anything under `data/private`, `.env` or `DECISIONS-private.md`, in any
  commit (it reads path names only, to count them).
- Writes to the repository, changes a ref or fetches.
- Prints a matched value.

## Done when

- Tests on a small made-up repository in a temporary folder pass: an amount in a file never
  appears in the report; a planted private file's contents are never read; an email is masked;
  an AI-tool commit on a side branch is found; a path is told apart as "at the tip" or "history
  only".
- Run on this repository, it writes `logs/scrub-report-<date>.md` and prints only counts.
- Yousef reads the report locally. The scrub itself is planned in a later session: a rewrite
  on a fresh clone pushed to a new empty repository (no force-push; the second round), created
  only after his go.
