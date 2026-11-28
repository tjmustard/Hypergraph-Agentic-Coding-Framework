"""Plain-text output."""

from collections.abc import Sequence

from stockroom.model import Item

HEADER = ("SKU", "NAME", "QTY", "REORDER")


def table(items: Sequence[Item]) -> str:
    """Return the items as an aligned table, or a line saying there are none."""
    if not items:
        return "no items"
    rows = [HEADER] + [
        (item.sku, item.name, str(item.quantity), str(item.reorder_level)) for item in items
    ]
    widths = [max(len(row[column]) for row in rows) for column in range(len(HEADER))]
    return "\n".join(
        "  ".join(cell.ljust(width) for cell, width in zip(row, widths)).rstrip() for row in rows
    )
