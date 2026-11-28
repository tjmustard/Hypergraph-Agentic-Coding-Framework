"""The command line. Exit 0 on success, 1 when the ledger refuses, 2 on bad usage."""

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from stockroom.errors import StockError
from stockroom.model import Item, Movement
from stockroom.report import table
from stockroom.store import Ledger


def build_parser() -> argparse.ArgumentParser:
    """Return the argument parser."""
    parser = argparse.ArgumentParser(prog="stockroom")
    parser.add_argument("--file", type=Path, default=Path("stock.json"), help="the ledger file")
    verbs = parser.add_subparsers(dest="verb", required=True)
    add = verbs.add_parser("add", help="add an item")
    add.add_argument("sku")
    add.add_argument("name")
    add.add_argument("--quantity", type=int, default=0)
    add.add_argument("--reorder-level", type=int, default=0)
    move = verbs.add_parser("move", help="change an item's quantity")
    move.add_argument("sku")
    move.add_argument("delta", type=int)
    move.add_argument("--note", default="")
    verbs.add_parser("show", help="list the items")
    return parser


def run(args: argparse.Namespace) -> str:
    """Carry out one parsed command and return what to print."""
    ledger = Ledger.load(args.file)
    if args.verb == "show":
        return table(ledger.items())
    if args.verb == "add":
        ledger.add_item(Item(args.sku, args.name, args.quantity, args.reorder_level))
        ledger.save(args.file)
        return f"added {args.sku}"
    item = ledger.apply(Movement(args.sku, args.delta, args.note))
    ledger.save(args.file)
    return f"{item.sku}: {item.quantity}"


def main(argv: Sequence[str] | None = None) -> int:
    """Run the command line and return its exit code."""
    args = build_parser().parse_args(argv)
    try:
        print(run(args))
    except StockError as error:
        print(f"stockroom: {error}", file=sys.stderr)
        return 1
    return 0
