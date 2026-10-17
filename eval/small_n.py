"""The small-sample rule for what the scripts that read a real account may print.

With few values, a median is one of the rows (or the mean of two), and a count of a handful says
what a person did. So: a median, quartile or gap statistic is shown only with 10 or more values,
and a group (a band, a type, a window) only with 5 or more rows; below that the script prints
"too few to show" (DECISIONS.md, 2026-10-12, the review of 2026-10-11).
"""

MIN_STATISTIC = 10  # a median or gap statistic needs at least this many values
MIN_GROUP = 5  # a group needs at least this many rows
TOO_FEW = "too few to show"
