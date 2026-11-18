"""Command-line interface for the AI Phishing Triage Bot.

Examples::

    python run.py --file data/input/07_phishing_credential_paypal.eml
    python run.py --folder data/input
    python run.py --folder data/input --json data/output/results.json --no-ai
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from app import __version__
from app.config import configure_logging, get_settings
from app.database.repository import ResultRepository
from app.email_parser.parser import EmailParseError
from app.models.schemas import Classification, TriageResult
from app.reporting import write_results_csv, write_results_json
from app.services.triage_service import TriageService

BANNER = """========================================
       AI PHISHING TRIAGE BOT
========================================"""

ACTION_LABELS = {
    Classification.PHISHING: "QUARANTINE",
    Classification.SUSPICIOUS: "REVIEW",
    Classification.BENIGN: "DELIVER",
}


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Analyse .eml files for phishing indicators",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--file", type=Path, help="Path to a single .eml file")
    source.add_argument("--folder", type=Path, help="Directory containing .eml files")
    parser.add_argument("--recursive", action="store_true", help="Scan subdirectories (with --folder)")
    parser.add_argument("--json", type=Path, default=None, help="Write full results JSON to this path")
    parser.add_argument("--csv", type=Path, default=None, help="Write CSV summary to this path")
    parser.add_argument("--no-ai", action="store_true", help="Disable LLM analysis")
    parser.add_argument("--no-db", action="store_true", help="Do not persist results")
    parser.add_argument("--verbose", action="store_true", help="Enable debug logging")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return parser.parse_args(argv)


def _print_result(result: TriageResult) -> None:
    print(f"File: {result.filename or 'raw-email'}")
    print()
    print(f"Classification: {result.risk.classification.value}")
    print(f"Risk Score:     {result.risk.risk_score}/100")
    print(f"Confidence:     {result.risk.confidence * 100:.0f}%")
    print()
    print("Reasons:")
    for reason in result.risk.reasons[:8]:
        print(f"- {reason}")
    print()
    if result.iocs:
        print("IOCs:")
        for ioc in result.iocs[:10]:
            print(f"- {ioc.value}  ({ioc.type.value})")
        if len(result.iocs) > 10:
            print(f"- ... and {len(result.iocs) - 10} more (see JSON report)")
    else:
        print("IOCs:\n- none extracted")
    print()
    if result.email.attachments:
        print("Attachments:")
        for attachment in result.email.attachments:
            flag = " [HIGH-RISK TYPE]" if attachment.is_dangerous_type else ""
            print(f"- {attachment.filename} ({attachment.content_type}, {attachment.size_bytes} bytes){flag}")
        print()
    print("Recommended Action:")
    print(ACTION_LABELS[result.risk.classification])
    print("=" * 40)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    base = get_settings()
    settings = base.model_copy(update={"ai_enabled": base.ai_enabled and not args.no_ai})
    configure_logging("DEBUG" if args.verbose else "WARNING")

    repository = None if args.no_db else ResultRepository(settings)
    service = TriageService(settings, repository=repository)

    if args.file:
        if not args.file.is_file():
            print(f"Error: file not found: {args.file}", file=sys.stderr)
            return 2
        print(BANNER)
        print()
        try:
            result = service.analyse_file(args.file, persist=not args.no_db)
        except EmailParseError as exc:
            print(f"Error: could not parse {args.file.name}: {exc}", file=sys.stderr)
            return 1
        _print_result(result)
        if args.json:
            write_results_json([result], args.json)
            print(f"JSON report: {args.json}")
        if args.csv:
            write_results_csv([result], args.csv)
            print(f"CSV report:  {args.csv}")
        return 0

    try:
        files = service.discover_files(args.folder, recursive=args.recursive)
    except FileNotFoundError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2
    if not files:
        print(f"No .eml files found in {args.folder}", file=sys.stderr)
        return 2

    print(BANNER)
    print()
    results: list[TriageResult] = []
    errors = 0
    for index, path in enumerate(files, start=1):
        try:
            result = service.analyse_file(path, persist=not args.no_db)
        except EmailParseError as exc:
            errors += 1
            print(f"[{index}/{len(files)}] {path.name}: ERROR ({exc})")
            continue
        results.append(result)
        print(
            f"[{index}/{len(files)}] {path.name}: "
            f"{result.risk.classification.value} ({result.risk.risk_score}/100)"
        )

    print()
    print("=" * 40)
    print(f"Total analysed: {len(results)}")
    print(f"Benign:         {sum(1 for r in results if r.risk.classification is Classification.BENIGN)}")
    print(f"Suspicious:     {sum(1 for r in results if r.risk.classification is Classification.SUSPICIOUS)}")
    print(f"Phishing:       {sum(1 for r in results if r.risk.classification is Classification.PHISHING)}")
    if errors:
        print(f"Errors:         {errors}")
    print("=" * 40)

    if args.json:
        write_results_json(results, args.json)
        print(f"JSON report: {args.json}")
    if args.csv:
        write_results_csv(results, args.csv)
        print(f"CSV report:  {args.csv}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
