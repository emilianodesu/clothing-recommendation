#!/usr/bin/env python3
"""Check local Markdown links in README.md and top-level docs/*.md.

By default, frozen historical subdirectories (`docs/archive/`, `docs/history/`,
and `docs/superpowers/`) are excluded so historical Q1-Q4 records remain
unmodified. Pass `--include-archives` to include those directories in an audit.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import sys

ARCHIVED_SUBDIRS = ("archive", "history", "superpowers")
_FENCE_RE = re.compile(r"^\s*(```+|~~~+)")
_INLINE_CODE_RE = re.compile(r"`[^`]*`")
_INLINE_LINK_RE = re.compile(r"!?\[[^\]]*\]\(([^)]+)\)")
_REF_DEF_RE = re.compile(r"^\s*\[[^\]]+\]:\s*(\S+)")
_EXTERNAL_SCHEMES = ("http://", "https://", "mailto:", "tel:", "data:")


def collect_markdown_files(root: Path, *, include_archives: bool = False) -> list[Path]:
    """Collect Markdown files in scope."""
    files: list[Path] = []
    readme = root / "README.md"
    if readme.is_file():
        files.append(readme)
    docs_dir = root / "docs"
    if docs_dir.is_dir():
        files.extend(sorted(docs_dir.glob("*.md")))
        if include_archives:
            for subdir in ARCHIVED_SUBDIRS:
                sub = docs_dir / subdir
                if sub.is_dir():
                    files.extend(sorted(sub.rglob("*.md")))
    return files


def _clean_target(raw: str) -> str:
    target = raw.strip()
    if target.startswith("<"):
        end = target.find(">")
        if end != -1:
            target = target[1:end].strip()
    else:
        # Strip optional title after whitespace, e.g. path/to/file.md "title"
        match = re.match(r'^(\S+)\s+["\'].*["\']\s*$', target)
        if match:
            target = match.group(1)
    return target.split("#", 1)[0].strip()


def check_file_links(md_file: Path, root: Path) -> tuple[int, list[dict[str, object]]]:
    """Return (local_link_count, broken_links) for a single Markdown file."""
    local_links = 0
    broken: list[dict[str, object]] = []
    in_fence = False
    fence_char = ""
    fence_len = 0

    lines = md_file.read_text(encoding="utf-8", errors="replace").splitlines()
    for line_no, line in enumerate(lines, start=1):
        fence_match = _FENCE_RE.match(line)
        if fence_match:
            marker = fence_match.group(1)
            if not in_fence:
                in_fence = True
                fence_char = marker[0]
                fence_len = len(marker)
                continue
            if marker[0] == fence_char and len(marker) >= fence_len:
                in_fence = False
                fence_char = ""
                fence_len = 0
                continue
        if in_fence:
            continue

        stripped_line = _INLINE_CODE_RE.sub("", line)
        raw_targets = [m.group(1) for m in _INLINE_LINK_RE.finditer(stripped_line)]
        ref_match = _REF_DEF_RE.match(stripped_line)
        if ref_match:
            raw_targets.append(ref_match.group(1))

        for raw_target in raw_targets:
            target = _clean_target(raw_target)
            if not target or target.startswith("#"):
                continue
            if target.lower().startswith(_EXTERNAL_SCHEMES):
                continue
            local_links += 1
            resolved = (md_file.parent / target).resolve()
            if not resolved.exists():
                try:
                    rel_file = str(md_file.relative_to(root))
                except ValueError:
                    rel_file = str(md_file)
                broken.append({
                    "file": rel_file,
                    "line": line_no,
                    "target": raw_target.strip(),
                    "resolved": str(resolved),
                })
    return local_links, broken


def scan_docs(root: Path, *, include_archives: bool = False) -> dict[str, object]:
    """Scan Markdown files under root and return structured link check report."""
    root = root.resolve()
    files = collect_markdown_files(root, include_archives=include_archives)
    total_local_links = 0
    broken_links: list[dict[str, object]] = []
    for md_file in files:
        count, broken = check_file_links(md_file, root)
        total_local_links += count
        broken_links.extend(broken)
    return {
        "root": str(root),
        "include_archives": include_archives,
        "excluded_subdirs": [] if include_archives else [f"docs/{s}/" for s in ARCHIVED_SUBDIRS],
        "files_checked": len(files),
        "local_links_checked": total_local_links,
        "broken_count": len(broken_links),
        "broken_links": broken_links,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Validate local Markdown links in README.md and top-level docs/*.md. "
            "Frozen historical directories (docs/archive/, docs/history/, docs/superpowers/) "
            "are excluded by default unless --include-archives is passed."
        )
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
        help="Repository root directory (defaults to parent of scripts/).",
    )
    parser.add_argument(
        "--include-archives",
        action="store_true",
        help="Also scan frozen historical docs/archive/, docs/history/, and docs/superpowers/.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print machine-readable JSON output.",
    )
    args = parser.parse_args(argv)

    report = scan_docs(args.root, include_archives=args.include_archives)
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        scope_note = (
            "including frozen archives"
            if args.include_archives
            else "excluding frozen docs/archive/, docs/history/, docs/superpowers/"
        )
        print(
            f"Checked {report['files_checked']} markdown files "
            f"({report['local_links_checked']} local links, {scope_note}): "
            f"{report['broken_count']} broken links."
        )
        for item in report["broken_links"]:
            print(f"  {item['file']}:{item['line']} -> {item['target']}")

    return 0 if report["broken_count"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
