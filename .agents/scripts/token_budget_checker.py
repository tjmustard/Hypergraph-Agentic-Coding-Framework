#!/usr/bin/env python3
"""Token Budget Checker

Enforces output token budgets per MiniPRD to prevent runaway task costs.
Checks estimated vs. ceiling (50k), suggests task splitting, tracks variance.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import List

import _runtime
import hyper_openrig
import hyper_spec


def normalize_key(identifier: str) -> str:
    """Return the budget key of a MiniPRD name or path, a slice dot-ID, or a slice dir or SPEC.md.

    A dot-ID or a bare legacy name is its own key and a MiniPRD path its file stem. A slice
    directory or its SPEC.md becomes the frontmatter `id`, or the directory name without one.
    """
    if hyper_openrig.DOT_ID_RE.match(identifier):
        return identifier
    path = Path(identifier)
    if path.name == "SPEC.md":
        path = path.parent
    spec = path / "SPEC.md"
    if spec.is_file():
        try:
            front = hyper_spec.parse_frontmatter(spec.read_text(encoding="utf-8")).data or {}
        except hyper_spec.SpecError:
            front = {}
        slice_id = front.get("id")
        return slice_id if isinstance(slice_id, str) else path.name
    return path.stem if path.suffix == ".md" else path.name


@dataclass
class BudgetCheckResult:
    """Result of budget check against 50k ceiling."""

    miniprd_name: str
    estimated_tokens: int
    ceiling: int
    flag: bool
    suggested_subtasks: List[str]
    message: str = ""

    def __post_init__(self):
        if self.estimated_tokens > self.ceiling:
            self.flag = True
            self.message = f"Budget exceeds 50k ceiling: {self.estimated_tokens:,} tokens estimated"
        else:
            self.flag = False
            self.message = (
                f"Budget within limit: {self.estimated_tokens:,} / {self.ceiling:,} tokens"
            )


class BudgetChecker:
    """Checks and enforces output token budgets for MiniPRDs."""

    BUDGET_CEILING = 50000

    def check(
        self, miniprd_name: str, estimated_output_tokens: int, verbose: bool = True
    ) -> BudgetCheckResult:
        """Check if estimated output tokens exceed 50k ceiling.

        Args:
            miniprd_name: MiniPRD name or path, slice dot-ID, or slice dir or SPEC.md path
            estimated_output_tokens: Developer's estimated output token count
            verbose: Log check result

        Returns:
            BudgetCheckResult with flag, message, and split suggestions
        """
        miniprd_name = normalize_key(miniprd_name)
        result = BudgetCheckResult(
            miniprd_name=miniprd_name,
            estimated_tokens=estimated_output_tokens,
            ceiling=self.BUDGET_CEILING,
            flag=False,
            suggested_subtasks=[],
        )

        if estimated_output_tokens > self.BUDGET_CEILING:
            result.flag = True
            result.suggested_subtasks = self._generate_split_suggestions(miniprd_name)

        if verbose:
            self._log_check(result)

        return result

    def estimate_tokens(
        self, code_lines: int, doc_pages: int, test_cases: int, verbose: bool = True
    ) -> int:
        """Estimate output tokens using heuristic formula.

        Formula: code_lines / 40 + doc_pages * 500 + test_cases * 100
        - ~40 tokens per line of code
        - ~500 tokens per documentation page
        - ~100 tokens per test case

        Args:
            code_lines: Estimated lines of code to generate
            doc_pages: Estimated documentation pages
            test_cases: Estimated number of test cases
            verbose: Log estimation

        Returns:
            Estimated output token count
        """
        code_tokens = code_lines / 40
        doc_tokens = doc_pages * 500
        test_tokens = test_cases * 100
        total = int(code_tokens + doc_tokens + test_tokens)

        if verbose:
            print(
                f"ℹ️  Token estimate: {code_lines} lines @ 40/line + {doc_pages} pages @ 500/page + {test_cases} tests @ 100/test"
            )
            print(
                f"    = {int(code_tokens)} + {int(doc_tokens)} + {int(test_tokens)} = {total:,} tokens"
            )

        return total

    def _log_check(self, result: BudgetCheckResult) -> None:
        """Log budget check result."""
        status = "❌ FLAG" if result.flag else "✅ PASS"
        print(f"{status}: {result.message}")

        if result.flag and result.suggested_subtasks:
            print("\nSuggested splits:")
            for subtask in result.suggested_subtasks:
                print(f"  - {subtask}")

    def _generate_split_suggestions(self, miniprd_name: str) -> List[str]:
        """Generate suggestions for splitting oversized MiniPRDs."""
        return [
            "[A] Core logic and data structures",
            "[B] API endpoints and integrations",
            "[C] Testing and documentation",
        ]


if __name__ == "__main__":
    import sys

    _runtime.require_runtime()
    if len(sys.argv) < 4:
        print("Usage: python3 token_budget_checker.py <code_lines> <doc_pages> <test_cases>")
        print("Example: python3 token_budget_checker.py 200 5 10")
        print("\nAlternatively:")
        print("Usage: python3 token_budget_checker.py check <spec> <estimated_tokens>")
        print("  <spec>: a MiniPRD name or path, a slice dot-ID, or a slice dir or SPEC.md path")
        print("Example: python3 token_budget_checker.py check MiniPRD_Example 12000")
        print("Example: python3 token_budget_checker.py check OPR.0.6.0.4 12000")
        sys.exit(1)

    if sys.argv[1] == "check":
        miniprd_name = sys.argv[2]
        try:
            estimated_tokens = int(sys.argv[3])
        except ValueError:
            print(f"❌ Invalid token count: {sys.argv[3]}")
            sys.exit(1)

        checker = BudgetChecker()
        result = checker.check(miniprd_name, estimated_tokens, verbose=True)
        sys.exit(0 if not result.flag else 1)
    else:
        try:
            code_lines = int(sys.argv[1])
            doc_pages = int(sys.argv[2])
            test_cases = int(sys.argv[3])
        except ValueError:
            print("❌ Invalid arguments (must be integers)")
            sys.exit(1)

        checker = BudgetChecker()
        estimated = checker.estimate_tokens(code_lines, doc_pages, test_cases, verbose=True)
        sys.exit(0)
