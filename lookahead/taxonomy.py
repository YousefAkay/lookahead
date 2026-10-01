"""The fixed label sets: 17 spending categories and the flow types used by the forecast.

"Personal care" (barbers, salons, beauty stores) and "Gifts & donations" (charities,
foundations, religious donations, gifts) were added during labelling; see DECISIONS.md.
"""

CATEGORIES: tuple[str, ...] = (
    "Income",
    "Rent & housing",
    "Groceries",
    "Restaurants & cafés",
    "Food delivery",
    "Transport",
    "Phone, internet & utilities",
    "Subscriptions",
    "Shopping",
    "Health & pharmacy",
    "Personal care",
    "Education",
    "Entertainment",
    "Gifts & donations",
    "Transfers",
    "Fees & interest",
    "Cash",
)

# How a transaction affects the forecast (separate from what it was for).
FLOW_TYPES: tuple[str, ...] = (
    "income",
    "recurring_outflow",
    "everyday",
    "transfer",
    "refund",
    "planned",
)


def check_labels(category: str, flow_type: str) -> None:
    """Raise ValueError if the category or flow type is not in the fixed sets."""
    if category not in CATEGORIES:
        raise ValueError(f"unknown category {category!r}")
    if flow_type not in FLOW_TYPES:
        raise ValueError(f"unknown flow_type {flow_type!r}")
