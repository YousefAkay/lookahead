# Safe-to-spend honesty check (synthetic development personas)

Made with `python -m eval.safe_to_spend_check`. Method in DECISIONS.md (2026-10-11, safe-to-spend). 12-month set, development personas only, the 40 weekly backtest origins: 800 origins, 20 personas. Stayed above = the observed closing balance was at or above the buffer on every day of the window.

| Origins | Stayed above the buffer |
|---|---|
| a safe amount, actual everyday spending at or below it | 98% (665 of 678, 20 personas) |
| a safe amount, actual everyday spending above it | 18% (7 of 39, 9 personas) |
| no safe amount | 2% (2 of 83, 7 personas) |
| all | 84% (674 of 800, 20 personas) |

## By archetype (a safe amount, spending at or below it)

| Archetype | Stayed above |
|---|---|
| DHOME | 100% (117 of 117, 3 personas) |
| DRENT | 97% (87 of 90, 3 personas) |
| FREE | 100% (40 of 40, 1 personas) |
| IFAM | 100% (120 of 120, 3 personas) |
| ITIGHT | 88% (69 of 78, 3 personas) |
| SAL | 100% (120 of 120, 3 personas) |
| SEM | 100% (40 of 40, 1 personas) |
| TIGHT | 99% (72 of 73, 3 personas) |

Safe amount against the usual everyday spending (origins with both): median 10.6 times (n = 717).

Origins overlap within a persona; the persona counts are the honest n.
