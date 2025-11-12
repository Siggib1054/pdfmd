"""Command-line interface for pdf_to_md.

Usage examples:
  pdfmd input.pdf                         # writes input.md next to PDF
  pdfmd input.pdf -o notes.md             # choose output path
  pdfmd input.pdf --ocr auto              # auto-detect scanned; use OCR if needed
  pdfmd input.pdf --ocr tesseract --export-images --page-breaks
  pdfmd input.pdf --callouts              # convert 'Note:/Warning:' blocks to callouts
  pdfmd input.pdf --nonbreaking-abbrev "i. e.,z. B.,u. a."  # protect multilingual abbrev splits

Exit codes: 0 on success, 1 on error.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Optional

# Flexible imports for both package and script execution
try:
    from pdf_to_md.models import Options  # type: ignore
    from pdf_to_md.pipeline import pdf_to_markdown  # type: ignore
except Exception:
    # Fallback to local imports when running from repo root
    from models import Options  # type: ignore
    from pipeline import pdf_to_markdown  # type: ignore


OCR_CHOICES = ("off", "auto", "tesseract", "ocrmypdf")


def _derive_output_path(input_pdf: Path, explicit_out: Optional[str]) -> Path:
    if explicit_out:
        return Path(explicit_out)
    return input_pdf.with_suffix(".md")


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="pdfmd",
        description="Convert PDF to clean Markdown (with optional OCR)",
    )

    # I/O
    p.add_argument("input", help="input PDF file")
    p.add_argument("-o", "--output", help="output Markdown path (.md)")

    # Core pipeline
    p.add_argument("--ocr", choices=OCR_CHOICES, default="off", help="OCR mode (default: off)")
    p.add_argument("--preview", action="store_true", help="process only first 3 pages")

    # Output features
    p.add_argument("--export-images", action="store_true",
                   help="export page images to <output>_assets/")
    p.add_argument("--page-breaks", action="store_true",
                   help="insert '---' between pages")

    # Transform toggles (existing behavior)
    p.add_argument("--keep-edges", action="store_true",
                   help="keep repeating headers/footers (do not remove)")
    p.add_argument("--no-caps-to-headings", dest="caps_to_headings", action="store_false",
                   help="do not promote ALL-CAPS to headings")
    p.set_defaults(caps_to_headings=True)

    p.add_argument("--no-defrag", dest="defragment", action="store_false",
                   help="disable orphan defragment")
    p.set_defaults(defragment=True)

    p.add_argument("--heading-size-ratio", type=float, default=1.15,
                   help="= body x ratio -> heading (default: 1.15)")
    p.add_argument("--orphan-max-len", "--orphan-len", dest="orphan_max_len", type=int, default=45,
                   help="max length (chars) of orphan to merge (default: 45)")

    # --- New: Reflow / unwrap controls ---
    p.add_argument("--unwrap-hyphens", dest="unwrap_hyphens", action="store_true",
                   help="join words split by hyphen + newline")
    p.add_argument("--no-unwrap-hyphens", dest="unwrap_hyphens", action="store_false")
    p.set_defaults(unwrap_hyphens=True)

    p.add_argument("--aggressive-hyphen", action="store_true",
                   help="also join TitleCase hyphenation across lines")

    p.add_argument("--reflow-soft-breaks", dest="reflow_soft_breaks", action="store_true",
                   help="reflow single newlines (sentence-aware) into spaces")
    p.add_argument("--no-reflow-soft-breaks", dest="reflow_soft_breaks", action="store_false")
    p.set_defaults(reflow_soft_breaks=True)

    p.add_argument("--protect-code", dest="protect_code", action="store_true",
                   help="skip unwrap/reflow inside fenced code blocks")
    p.add_argument("--no-protect-code", dest="protect_code", action="store_false")
    p.set_defaults(protect_code=True)

    # --- New: Callouts + multilingual abbreviation safety ---
    p.add_argument("--callouts", dest="enable_callouts", action="store_true",
                   help="convert simple 'Label:\nBody' blocks (e.g. 'Note:') to Obsidian callouts")
    p.add_argument("--no-callouts", dest="enable_callouts", action="store_false",
                   help="disable callout conversion")
    p.set_defaults(enable_callouts=True)

    p.add_argument("--nonbreaking-abbrev", dest="nonbreaking_abbrev", metavar="LIST",
                   help="comma-separated list (e.g. 'i. e.,z. B.,u. a.') to keep together across lines")
    p.add_argument("--no-nonbreaking-abbrev", dest="nonbreaking_abbrev", action="store_const", const="",
                   help="disable extra non-breaking abbreviations")

    # UX
    p.add_argument("--quiet", action="store_true", help="suppress log output")
    p.add_argument("--no-progress", action="store_true", help="suppress progress bar")
    return p


def main(argv: Optional[list[str]] = None) -> int:
    args = _build_parser().parse_args(argv)

    inp = Path(args.input)
    if not inp.exists():
        print(f"Input not found: {inp}", file=sys.stderr)
        return 1
    if inp.suffix.lower() != ".pdf":
        print("Input must be a .pdf file", file=sys.stderr)
        return 1

    outp = _derive_output_path(inp, args.output)

    # Construct options with known fields
    opts = Options(
        ocr_mode=args.ocr,
        preview_only=bool(args.preview),
        caps_to_headings=bool(args.caps_to_headings),
        defragment_short=bool(args.defragment),
        heading_size_ratio=float(args.heading_size_ratio),
        orphan_max_len=int(args.orphan_max_len),
        remove_headers_footers=not bool(args.keep_edges),
        insert_page_breaks=bool(args.page_breaks),
        export_images=bool(args.export_images),
    )

    # Forward-compatible: set new attributes even if older Options doesn't declare them
    setattr(opts, "unwrap_hyphens", bool(args.unwrap_hyphens))
    setattr(opts, "aggressive_hyphen", bool(args.aggressive_hyphen))
    setattr(opts, "reflow_soft_breaks", bool(args.reflow_soft_breaks))
    setattr(opts, "protect_code", bool(args.protect_code))
    setattr(opts, "enable_callouts", bool(args.enable_callouts))

    if args.nonbreaking_abbrev is not None:
        extra = [s.strip() for s in args.nonbreaking_abbrev.split(",") if s.strip()]
        setattr(opts, "non_breaking_abbrevs", extra)

    def log_cb(msg: str):
        if not args.quiet:
            print(msg)

    def progress_cb(done: int, total: int):
        if args.no_progress:
            return
        try:
            pct = int((done / total) * 100) if total else 0
        except Exception:
            pct = 0
        bar_width = 28
        filled = int(bar_width * pct / 100)
        bar = "#" * filled + "-" * (bar_width - filled)
        sys.stderr.write(f"\r[{bar}] {pct:3d}%")
        sys.stderr.flush()
        if pct >= 100:
            sys.stderr.write("\n")

    try:
        pdf_to_markdown(str(inp), str(outp), opts, progress_cb=progress_cb, log_cb=log_cb)
    except Exception as e:
        if not args.no_progress:
            sys.stderr.write("\n")
        print(f"Error: {e}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
