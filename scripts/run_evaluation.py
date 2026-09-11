#!/usr/bin/env python
"""Run the EasyCode evaluation: vector-only baseline vs graph + vector hybrid.

    python scripts/run_evaluation.py --benchmark miniapp
    python scripts/run_evaluation.py --benchmark requests --output docs/results

Requires Qdrant and Neo4j (``docker compose up -d qdrant neo4j``). Generation
uses whichever LLM provider is configured; pass ``--no-generate`` to measure
retrieval only, which needs no LLM at all.
"""

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.app.evaluation.benchmark import BENCHMARKS  # noqa: E402
from backend.app.evaluation.runner import format_report, run_benchmark, save_report  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--benchmark",
        default="miniapp",
        choices=sorted(BENCHMARKS),
        help="Which benchmark to run (default: miniapp, the deterministic fixture)",
    )
    parser.add_argument(
        "--systems",
        default="vector,hybrid",
        help="Comma-separated systems to evaluate (default: vector,hybrid)",
    )
    parser.add_argument(
        "--no-generate",
        action="store_true",
        help="Measure retrieval only; skips the LLM entirely",
    )
    parser.add_argument(
        "--reindex",
        action="store_true",
        help="Re-index the benchmark repository instead of reusing an indexed copy",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("docs/results"),
        help="Directory for the JSON and Markdown reports",
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)-8s %(message)s")
    for noisy in ("httpx", "httpcore", "sentence_transformers", "neo4j", "urllib3"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    report = run_benchmark(
        BENCHMARKS[args.benchmark],
        systems=tuple(s.strip() for s in args.systems.split(",") if s.strip()),
        generate=not args.no_generate,
        reuse_existing=not args.reindex,
    )

    print()
    print(format_report(report))

    json_path, markdown_path = save_report(report, args.output)
    print(f"Wrote {json_path}")
    print(f"Wrote {markdown_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
