"""The fixed label sets: 15 spending categories and the flow types used by the forecast."""

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
    "Education",
    "Entertainment",
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
