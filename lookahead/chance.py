"""How a chance read from the simulated futures is written, everywhere it is shown.

The forecast runs 500 futures, so a chance is a count out of 500. Rounded to a whole percent,
499 of 500 would read "100%" and 1 of 500 "0%", which sound certain when they are not. So "100%"
means every future and "0%" means none; anything that would round to them reads ">99%" or "<1%".
"""


def chance_text(share: float) -> str:
    """A share of simulated futures as a percent: 0.42 -> "42%", 0.998 -> ">99%", 1.0 -> "100%"."""
    if share >= 1:
        return "100%"
    if share <= 0:
        return "0%"
    text = f"{share:.0%}"
    if text == "100%":
        return ">99%"
    if text == "0%":
        return "<1%"
    return text
