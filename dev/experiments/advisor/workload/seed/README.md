# stockroom

A tiny stock ledger kept in one JSON file. Standard library only.

    python3 -m stockroom --file stock.json add BOLT "M4 bolt" --quantity 40 --reorder-level 10
    python3 -m stockroom --file stock.json move BOLT -5 --note "job 12"
    python3 -m stockroom --file stock.json show

Run the tests with `uv run --locked pytest -q`.

- `stockroom/model.py`: the records.
- `stockroom/store.py`: the ledger, its rules and its file.
- `stockroom/report.py`: text output.
- `stockroom/cli.py`: the command line; the only module that prints.
