# Ask: persona sheet for writing held-out questions

Six made-up development personas (synthetic data only). Every question is asked on 2026-09-15
(a Tuesday); each persona's data ends on 2026-09-14. The forecast covers 2026-09-15 to
2026-10-14.

How to write them (spec docs/specs/2026-11-20-ask.md, step 9):
- Write 50 questions the way you would really type them, in English or French (about 1 in 7 in
  French), each for one persona below. Put the persona id before each question.
- Mix roughly as the spec says: half lookups (totals, averages, counts, top N, comparisons, a few
  typos or casual wording), some forecasts and what-ifs, a few budget, safe-to-spend and
  recurring-payment questions, a few ambiguous ones, and a few it should refuse (advice,
  opinions, things not in the data, past the 30-day forecast).
- Do not read eval/ask_dev_set.py first, so your questions are your own wording.
- Write only the questions; the gold plans are written separately (two subagents), and you
  review where they disagree.
- Counts in brackets are the number of rows, so you can tell what is common.

## v2-SAL-01

Data from 2025-10-03 to 2026-09-14; today is 2026-09-15.
- Categories: Groceries (132), Restaurants & cafés (111), Shopping (68), Transport (35), Income (25), Health & pharmacy (24), Phone, internet & utilities (22), Subscriptions (12), Rent & housing (12), Cash (8), Entertainment (1)
- Merchants: IGA (59), Restaurant Le Petit Orme (57), Café Velisaro (54), Amazon.ca (52), Metro (50), Bixi (35), Clinique Verdun (25), Pharmaprix (24), SAQ (23), Winners (13), Interac e-Transfer (12), Spotify (12), Hydro-Québec (11), Vidéotron (11), ABM (8), Etsy (2), Ticketmaster (1), uBreakiFix (1)
- Recurring payments: Clinique Verdun, Hydro-Québec, Spotify, Vidéotron
- Budgets this month: Restaurants & cafés $250, Shopping $280

## v2-FREE-01

Data from 2025-10-02 to 2026-09-14; today is 2026-09-15.
- Categories: Groceries (125), Restaurants & cafés (93), Shopping (56), Phone, internet & utilities (36), Transport (34), Income (31), Health & pharmacy (29), Rent & housing (12), Cash (6), Fees & interest (2)
- Merchants: IGA (57), Café Velisaro (52), Metro (47), Restaurant Le Petit Orme (41), Amazon.ca (37), Bixi (34), Interac e-Transfer (31), Pharmaprix (29), SAQ (21), Winners (15), Bell Internet (12), Gestion Immobilière (12), Hydro-Québec (12), Vidéotron (12), ABM (6), Etsy (3), Scotiabank (2), Best Buy (1)
- Recurring payments: Bell Internet, Gestion Immobiliere, Hydro-Québec, Vidéotron
- Budgets this month: Restaurants & cafés $170, Shopping $290

## v2-TIGHT-01

Data from 2025-10-01 to 2026-09-14; today is 2026-09-15.
- Categories: Groceries (151), Restaurants & cafés (104), Shopping (59), Transport (47), Health & pharmacy (35), Income (25), Subscriptions (23), Fees & interest (13), Rent & housing (12), Phone, internet & utilities (11), Cash (6), Education (1)
- Merchants: IGA (64), Café Velisaro (59), Metro (53), Bixi (47), Restaurant Le Petit Orme (45), Amazon.ca (41), Pharmaprix (35), SAQ (34), Groupe Conseil Plateau (25), Scotiabank (13), Winners (13), Interac e-Transfer (12), Spotify (12), Fizz (11), Netflix (11), ABM (6), Etsy (5), Concordia Bookstore (1)
- Recurring payments: Fizz, Groupe Conseil Plateau, Netflix, Spotify
- Budgets this month: Restaurants & cafés $200, Shopping $105

## v2-DRENT-01

Data from 2025-10-01 to 2026-09-14; today is 2026-09-15.
- Categories: Food delivery (158), Groceries (99), Restaurants & cafés (96), Subscriptions (58), Income (37), Transport (35), Rent & housing (24), Phone, internet & utilities (24), Shopping (18), Cash (18), Education (5), Fees & interest (3)
- Merchants: Uber Eats (128), Tim Hortons (56), Maxi (41), Café Quorvel (40), Couche-Tard (38), STM (35), DoorDash (30), Atelier Jarry (25), Super C (20), ABM (18), Dollarama (18), Apple iCloud (12), BMO Bank of Montreal (12), Fizz (12), Gestion Immobilière (12), Hydro-Québec (12), Intact Insurance (12), Netflix (12), YouTube Premium (12), Disney Plus (11), Spotify (11), Concordia University (3), Scotiabank (3), Concordia Bookstore (2)
- Recurring payments: Apple Icloud, Atelier Jarry, Bmo Bank Of, Disney Plus, Fizz, Gestion Immobiliere, Hydro-Québec, Intact Insurance, Netflix, STM, Spotify, Youtube Premium
- Budgets this month: Food delivery $225, Restaurants & cafés $65, Shopping $15

## v2-DHOME-01

Data from 2025-10-02 to 2026-09-14; today is 2026-09-15.
- Categories: Food delivery (319), Restaurants & cafés (173), Groceries (167), Transport (51), Subscriptions (46), Income (25), Shopping (23), Phone, internet & utilities (22), Cash (12), Health & pharmacy (11)
- Merchants: Uber Eats (245), Tim Hortons (113), DoorDash (74), Couche-Tard (65), Café Quorvel (60), Maxi (60), STM (51), Super C (42), Cafe Plateau Inc (25), Dollarama (21), ABM (12), Amazon Prime (12), Disney Plus (12), Apple iCloud (11), Bell Internet (11), Fizz (11), Netflix (11), Sunlife Student Health (11), Best Buy (2)
- Recurring payments: Amazon Prime, Apple Icloud, Bell Internet, Cafe Plateau Inc, Disney Plus, Fizz, Netflix, STM, Sunlife Student Health
- Budgets this month: Food delivery $555, Restaurants & cafés $60, Shopping $40

## v2-IFAM-01

Data from 2025-10-01 to 2026-09-14; today is 2026-09-15.
- Categories: Food delivery (176), Restaurants & cafés (109), Groceries (103), Subscriptions (59), Phone, internet & utilities (34), Transport (30), Rent & housing (23), Fees & interest (17), Income (14), Shopping (13), Health & pharmacy (11), Cash (9), Education (2), Entertainment (1)
- Merchants: Uber Eats (119), Tim Hortons (60), DoorDash (57), Café Quorvel (49), Maxi (46), Couche-Tard (33), STM (30), Super C (24), Scotiabank (17), Bell Internet (12), Disney Plus (12), Gestion Immobilière (12), Nadia Haddad (12), Netflix (12), Spotify (12), YouTube Premium (12), Crave (11), Dollarama (11), Fizz (11), Hydro-Québec (11), Intact Insurance (11), Sunlife Student Health (11), ABM (9), Concordia University (2), Karim Haddad (2), Best Buy (1), Ticketmaster (1), uBreakiFix (1)
- Recurring payments: Bell Internet, Crave, Disney Plus, Fizz, Gestion Immobiliere, Hydro-Québec, Intact Insurance, Nadia Haddad, Netflix, Sunlife Student Health, Youtube Premium
- Budgets this month: Food delivery $465, Restaurants & cafés $70, Shopping $15
