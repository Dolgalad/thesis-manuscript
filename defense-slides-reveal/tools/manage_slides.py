#!/usr/bin/env python3
"""
manage_slides_safe.py

Idempotent Reveal.js slide manager.

Design principle:
  format/renumber must be safe to run repeatedly.
  One run computes one canonical deck state, writes exactly that state,
  removes duplicate/orphan slide files, and rebuilds css/slides.css imports.

Expected structure:
  slides/*.html
  css/slides/*.css
  css/slides.css

Canonical naming:
  normal slide with data-section and data-title:
    NN-section-title.html
  title slide class="title-slide":
    NN-title.html
  divider slide with section-divider-slide attribute:
    NN-title-or-section.html
  when data-section == data-title:
    NN-title.html

All mutating commands support --simulate.
"""

from __future__ import annotations

import argparse
import datetime as dt
import html
import re
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

PROJECT_ROOT = Path.cwd()
SLIDES_DIR = PROJECT_ROOT / "slides"
CSS_DIR = PROJECT_ROOT / "css"
CSS_SLIDES_DIR = CSS_DIR / "slides"
CSS_MANIFEST = CSS_DIR / "slides.css"

IMPORT_BEGIN = "/* BEGIN AUTO SLIDE IMPORTS */"
IMPORT_END = "/* END AUTO SLIDE IMPORTS */"

SECTION_RE = re.compile(r"<section\b[^>]*>.*?</section>", flags=re.I | re.S)
OPEN_SECTION_RE = re.compile(r"<section\b[^>]*>", flags=re.I | re.S)
IMPORT_RE = re.compile(r"""^\s*@import\s+(?:url\()?['\"](?P<path>\./slides/[^'\")]+\.css)['\"]\)?\s*;\s*$""", flags=re.I | re.M)
ANY_SLIDE_IMPORT_RE = re.compile(r"""\s*@import\s+(?:url\()?['\"]\./slides/[^'\")]+\.css['\"]\)?\s*;\s*\n?""", flags=re.I)

VOID_TAGS = {
    "area", "base", "br", "col", "embed", "hr", "img", "input", "link",
    "meta", "param", "source", "track", "wbr",
}
RAW_TEXT_TAGS = {"script", "style", "pre", "code", "textarea"}


@dataclass(frozen=True)
class SlideSource:
    path: Path
    order_number: Optional[int]
    order_width: Optional[int]
    old_slug: str
    content: str
    section: str
    canonical_slug: str


@dataclass(frozen=True)
class CanonicalSlide:
    index: int
    slug: str
    html_name: str
    css_name: str
    source: SlideSource
    css_source: Optional[Path]
    css_content: Optional[str]


@dataclass(frozen=True)
class WriteOp:
    path: Path
    content: str


@dataclass(frozen=True)
class DeleteOp:
    path: Path


# ---------------------------------------------------------------------
# Basic helpers
# ---------------------------------------------------------------------

def die(message: str, code: int = 1) -> None:
    print(f"ERROR: {message}", file=sys.stderr)
    raise SystemExit(code)


def ensure_dirs() -> None:
    SLIDES_DIR.mkdir(parents=True, exist_ok=True)
    CSS_SLIDES_DIR.mkdir(parents=True, exist_ok=True)


def parse_numbered_stem(path: Path, suffix: str) -> tuple[Optional[int], Optional[int], str]:
    if path.suffix != suffix:
        return None, None, path.stem
    match = re.match(r"^(\d+)-(.+)$", path.stem)
    if not match:
        return None, None, path.stem
    raw_number = match.group(1)
    return int(raw_number), len(raw_number), match.group(2)


def detect_number_width(paths: Iterable[Path], explicit: Optional[int] = None) -> int:
    if explicit is not None:
        return explicit

    counts: dict[int, int] = {}
    for path in paths:
        _, width, _ = parse_numbered_stem(path, path.suffix)
        if width is not None:
            counts[width] = counts.get(width, 0) + 1

    if not counts:
        return 2

    # Most common width. Ties prefer the smaller width, so 02 wins over 002 if mixed.
    return sorted(counts.items(), key=lambda item: (-item[1], item[0]))[0][0]


def natural_file_order(path: Path) -> tuple[int, int, str]:
    number, _, _ = parse_numbered_stem(path, path.suffix)
    if number is None:
        return (1, 10**9, path.name)
    return (0, number, path.name)


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def normalize_newline(content: str) -> str:
    return content if content.endswith("\n") else content + "\n"


# ---------------------------------------------------------------------
# HTML metadata and formatting
# ---------------------------------------------------------------------

def opening_section_tag(section: str) -> str:
    match = OPEN_SECTION_RE.search(section)
    return match.group(0) if match else ""


def attr_value(section: str, attr: str) -> Optional[str]:
    tag = opening_section_tag(section)
    if not tag:
        return None
    match = re.search(rf"\b{re.escape(attr)}\s*=\s*(['\"])(.*?)\1", tag, flags=re.I | re.S)
    if not match:
        return None
    return html.unescape(match.group(2)).strip()


def has_attr(section: str, attr: str) -> bool:
    tag = opening_section_tag(section)
    return bool(re.search(rf"\b{re.escape(attr)}(?:\s*=|\s|>|/)", tag, flags=re.I))


def has_class(section: str, class_name: str) -> bool:
    classes = attr_value(section, "class") or ""
    return class_name in classes.split()


def extract_text(fragment: str) -> str:
    text = re.sub(r"<script\b[^>]*>.*?</script>", " ", fragment, flags=re.I | re.S)
    text = re.sub(r"<style\b[^>]*>.*?</style>", " ", text, flags=re.I | re.S)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def first_heading(section: str) -> Optional[str]:
    match = re.search(r"<h[1-3]\b[^>]*>(.*?)</h[1-3]>", section, flags=re.I | re.S)
    return extract_text(match.group(1)) if match else None


def slugify(text: Optional[str], fallback: str = "slide", max_words: int = 10) -> str:
    if not text:
        return fallback
    text = html.unescape(text).lower()
    text = re.sub(r"\$.*?\$", " ", text)
    text = re.sub(r"\\\w+(?:\{[^}]*\})?", " ", text)
    text = re.sub(r"[^a-z0-9]+", " ", text)
    words = [word for word in text.split() if word]
    return "-".join(words[:max_words]) if words else fallback


def slug_from_section(section: str, fallback: str) -> str:
    data_section = attr_value(section, "data-section")
    data_title = attr_value(section, "data-title")

    section_slug = slugify(data_section, fallback="") if data_section else ""
    title_slug = slugify(data_title, fallback="") if data_title else ""

    title_slide = has_class(section, "title-slide")
    divider_slide = has_attr(section, "section-divider-slide")

    if title_slide or divider_slide:
        return title_slug or section_slug or slugify(first_heading(section), fallback=fallback)

    if section_slug and title_slug:
        if section_slug == title_slug:
            return title_slug
        return f"{section_slug}-{title_slug}"

    return title_slug or section_slug or slugify(first_heading(section) or extract_text(section), fallback=fallback)


def unique_slugs(slugs: Iterable[str]) -> list[str]:
    seen: dict[str, int] = {}
    result: list[str] = []
    for slug in slugs:
        base = slug or "slide"
        count = seen.get(base, 0)
        result.append(base if count == 0 else f"{base}-{count + 1}")
        seen[base] = count + 1
    return result


def format_html_fragment(source: str, indent: str = "  ") -> str:
    source = source.strip()
    tokens = re.findall(r"<!--.*?-->|<!\[CDATA\[.*?\]\]>|</?[^>]+>|[^<]+", source, flags=re.S)

    lines: list[str] = []
    level = 0
    raw_stack: list[str] = []

    def pad() -> str:
        return indent * max(level, 0)

    for token in tokens:
        if not token:
            continue
        stripped = token.strip()
        if not stripped:
            continue

        if raw_stack:
            closing_raw = re.match(rf"</{re.escape(raw_stack[-1])}\s*>", stripped, flags=re.I)
            if closing_raw:
                level -= 1
                lines.append(pad() + stripped)
                raw_stack.pop()
            else:
                for raw_line in token.strip("\n").splitlines():
                    if raw_line.strip():
                        lines.append(pad() + raw_line.rstrip())
            continue

        close_match = re.match(r"</([a-zA-Z0-9:-]+)\s*>", stripped)
        if close_match:
            level -= 1
            lines.append(pad() + stripped)
            continue

        open_match = re.match(r"<([a-zA-Z0-9:-]+)(\s|>|/)", stripped)
        if open_match:
            tag = open_match.group(1).lower()
            is_comment = stripped.startswith("<!--")
            is_doctype = stripped.startswith("<!")
            is_inline_closed = re.search(rf"</{re.escape(tag)}\s*>", stripped, flags=re.I)
            is_self_closing = stripped.endswith("/>") or tag in VOID_TAGS or is_comment or is_doctype or bool(is_inline_closed)

            lines.append(pad() + stripped)
            if not is_self_closing:
                level += 1
                if tag in RAW_TEXT_TAGS:
                    raw_stack.append(tag)
            continue

        text = re.sub(r"\s+", " ", stripped)
        if text:
            lines.append(pad() + text)

    return "\n".join(line.rstrip() for line in lines if line.strip()) + "\n"


# ---------------------------------------------------------------------
# Planning
# ---------------------------------------------------------------------

def load_slide_sources() -> list[SlideSource]:
    if not SLIDES_DIR.exists():
        return []

    html_files = sorted(SLIDES_DIR.glob("*.html"), key=natural_file_order)
    sources: list[SlideSource] = []

    for fallback_index, path in enumerate(html_files, start=1):
        number, width, old_slug = parse_numbered_stem(path, ".html")
        content = read_text(path)
        sections = SECTION_RE.findall(content)

        if len(sections) != 1:
            # Keep it visible and fail instead of guessing. This prevents destructive cleanup.
            die(f"{path} must contain exactly one <section> block; found {len(sections)}.")

        section = sections[0]
        canonical_slug = slug_from_section(section, fallback=f"slide-{fallback_index}")
        sources.append(
            SlideSource(
                path=path,
                order_number=number,
                order_width=width,
                old_slug=old_slug,
                content=format_html_fragment(section),
                section=section,
                canonical_slug=canonical_slug,
            )
        )

    return sources


def source_order_key(source: SlideSource) -> tuple[int, int, str]:
    if source.order_number is None:
        return (1, 10**9, source.path.name)
    return (0, source.order_number, source.path.name)


def find_css_source(source: SlideSource, css_files: list[Path], used: set[Path], final_stem: str) -> Optional[Path]:
    candidates: list[Path] = []

    # Prefer exact current stem, then same number, then same old slug.
    exact_old = CSS_SLIDES_DIR / f"{source.path.stem}.css"
    if exact_old in css_files:
        candidates.append(exact_old)

    if source.order_number is not None:
        for css_path in css_files:
            number, _, _ = parse_numbered_stem(css_path, ".css")
            if number == source.order_number:
                candidates.append(css_path)

    for css_path in css_files:
        _, _, slug = parse_numbered_stem(css_path, ".css")
        if slug == source.old_slug or slug == source.canonical_slug or css_path.stem == final_stem:
            candidates.append(css_path)

    for candidate in candidates:
        if candidate in css_files and candidate not in used:
            used.add(candidate)
            return candidate

    return None


def build_canonical_deck(number_width: int, create_missing_css: bool = True) -> list[CanonicalSlide]:
    sources = sorted(load_slide_sources(), key=source_order_key)
    slugs = unique_slugs(source.canonical_slug for source in sources)
    css_files = sorted(CSS_SLIDES_DIR.glob("*.css"), key=natural_file_order) if CSS_SLIDES_DIR.exists() else []
    used_css: set[Path] = set()

    deck: list[CanonicalSlide] = []
    for index, (source, slug) in enumerate(zip(sources, slugs), start=1):
        stem = f"{index:0{number_width}d}-{slug}"
        html_name = f"{stem}.html"
        css_name = f"{stem}.css"
        css_source = find_css_source(source, css_files, used_css, stem)
        css_content = None
        if css_source is not None:
            css_content = normalize_newline(read_text(css_source))
        elif create_missing_css:
            css_content = f"/* Styles for {html_name} */\n"

        deck.append(
            CanonicalSlide(
                index=index,
                slug=slug,
                html_name=html_name,
                css_name=css_name,
                source=source,
                css_source=css_source,
                css_content=css_content,
            )
        )

    return deck


def import_lines_for_deck(deck: list[CanonicalSlide]) -> list[str]:
    return [f'@import "./slides/{slide.css_name}";' for slide in deck if slide.css_content is not None]


def clean_manifest(content: str, import_lines: list[str]) -> str:
    block = IMPORT_BEGIN + "\n"
    if import_lines:
        block += "\n".join(import_lines) + "\n"
    block += IMPORT_END

    if IMPORT_BEGIN in content and IMPORT_END in content:
        content = re.sub(re.escape(IMPORT_BEGIN) + r".*?" + re.escape(IMPORT_END), "", content, flags=re.S)

    # Remove every slide import anywhere in the manifest. Keep non-slide imports/comments.
    content = ANY_SLIDE_IMPORT_RE.sub("", content).strip()
    return block + ("\n\n" + content + "\n" if content else "\n")


def plan_rewrite(deck: list[CanonicalSlide]) -> tuple[list[WriteOp], list[DeleteOp]]:
    desired_html = {SLIDES_DIR / slide.html_name for slide in deck}
    desired_css = {CSS_SLIDES_DIR / slide.css_name for slide in deck if slide.css_content is not None}

    writes: list[WriteOp] = []
    deletes: list[DeleteOp] = []

    for slide in deck:
        writes.append(WriteOp(SLIDES_DIR / slide.html_name, slide.source.content))
        if slide.css_content is not None:
            writes.append(WriteOp(CSS_SLIDES_DIR / slide.css_name, slide.css_content))

    old_manifest = read_text(CSS_MANIFEST) if CSS_MANIFEST.exists() else ""
    writes.append(WriteOp(CSS_MANIFEST, clean_manifest(old_manifest, import_lines_for_deck(deck))))

    for path in sorted(SLIDES_DIR.glob("*.html"), key=natural_file_order) if SLIDES_DIR.exists() else []:
        if path not in desired_html:
            deletes.append(DeleteOp(path))

    for path in sorted(CSS_SLIDES_DIR.glob("*.css"), key=natural_file_order) if CSS_SLIDES_DIR.exists() else []:
        if path not in desired_css:
            deletes.append(DeleteOp(path))

    return writes, deletes


# ---------------------------------------------------------------------
# Transaction
# ---------------------------------------------------------------------

def backup_project() -> Optional[Path]:
    existing = [p for p in [SLIDES_DIR, CSS_SLIDES_DIR, CSS_MANIFEST] if p.exists()]
    if not existing:
        return None

    backup_root = PROJECT_ROOT / ".slide_manager_backups" / dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_root.mkdir(parents=True, exist_ok=True)

    if SLIDES_DIR.exists():
        shutil.copytree(SLIDES_DIR, backup_root / "slides")
    if CSS_SLIDES_DIR.exists():
        shutil.copytree(CSS_SLIDES_DIR, backup_root / "css" / "slides", dirs_exist_ok=True)
    if CSS_MANIFEST.exists():
        (backup_root / "css").mkdir(parents=True, exist_ok=True)
        shutil.copy2(CSS_MANIFEST, backup_root / "css" / "slides.css")

    return backup_root


def print_plan(title: str, writes: list[WriteOp], deletes: list[DeleteOp]) -> None:
    print(f"\n{title}")
    if not writes and not deletes:
        print("  No changes.")
        return

    if writes:
        print("\n  Write/replace:")
        for op in writes:
            print(f"    {op.path}")

    if deletes:
        print("\n  Delete duplicates/orphans:")
        for op in deletes:
            print(f"    {op.path}")


def apply_rewrite(writes: list[WriteOp], deletes: list[DeleteOp], simulate: bool, backup: bool) -> None:
    if simulate:
        return

    ensure_dirs()
    backup_path = backup_project() if backup else None

    # Write canonical files first. Then delete non-canonical files only.
    for op in writes:
        op.path.parent.mkdir(parents=True, exist_ok=True)
        op.path.write_text(normalize_newline(op.content), encoding="utf-8")

    for op in deletes:
        if op.path.exists():
            op.path.unlink()

    if backup_path:
        print(f"\nBackup written to: {backup_path}")


# ---------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------

def cmd_format(args: argparse.Namespace) -> None:
    html_paths = list(SLIDES_DIR.glob("*.html")) if SLIDES_DIR.exists() else []
    number_width = detect_number_width(html_paths, explicit=args.number_width)
    deck = build_canonical_deck(number_width=number_width, create_missing_css=not args.no_css)
    writes, deletes = plan_rewrite(deck)
    print_plan("Canonical format/cleanup", writes, deletes)
    apply_rewrite(writes, deletes, simulate=args.simulate, backup=not args.no_backup)


def cmd_sync_imports(args: argparse.Namespace) -> None:
    html_paths = list(SLIDES_DIR.glob("*.html")) if SLIDES_DIR.exists() else []
    number_width = detect_number_width(html_paths, explicit=args.number_width)
    deck = build_canonical_deck(number_width=number_width, create_missing_css=not args.no_css)
    old_manifest = read_text(CSS_MANIFEST) if CSS_MANIFEST.exists() else ""
    write = WriteOp(CSS_MANIFEST, clean_manifest(old_manifest, import_lines_for_deck(deck)))
    print_plan("Sync CSS manifest", [write], [])
    apply_rewrite([write], [], simulate=args.simulate, backup=not args.no_backup)


def cmd_check(args: argparse.Namespace) -> None:
    html_paths = list(SLIDES_DIR.glob("*.html")) if SLIDES_DIR.exists() else []
    number_width = detect_number_width(html_paths, explicit=args.number_width)
    deck = build_canonical_deck(number_width=number_width, create_missing_css=not args.no_css)
    writes, deletes = plan_rewrite(deck)

    dirty_writes = []
    for op in writes:
        if not op.path.exists() or read_text(op.path) != normalize_newline(op.content):
            dirty_writes.append(op)

    dirty_deletes = [op for op in deletes if op.path.exists()]

    if dirty_writes or dirty_deletes:
        print_plan("Project is not canonical. Run format to fix", dirty_writes, dirty_deletes)
        raise SystemExit(1)

    print("Slide structure looks canonical.")


def cmd_list(args: argparse.Namespace) -> None:
    html_paths = list(SLIDES_DIR.glob("*.html")) if SLIDES_DIR.exists() else []
    number_width = detect_number_width(html_paths, explicit=args.number_width)
    deck = build_canonical_deck(number_width=number_width, create_missing_css=not args.no_css)
    if not deck:
        print("No slides found.")
        return
    for slide in deck:
        print(f"{slide.index:0{number_width}d}  {slide.slug}  {slide.html_name}")


def renumbered_sources_from_current_order() -> list[SlideSource]:
    return sorted(load_slide_sources(), key=source_order_key)


def write_temp_sources_for_reorder(sources: list[SlideSource], number_width: int, create_missing_css: bool) -> list[CanonicalSlide]:
    # Rebuild directly from supplied sources instead of filesystem order.
    slugs = unique_slugs(source.canonical_slug for source in sources)
    css_files = sorted(CSS_SLIDES_DIR.glob("*.css"), key=natural_file_order) if CSS_SLIDES_DIR.exists() else []
    used_css: set[Path] = set()
    deck: list[CanonicalSlide] = []
    for index, (source, slug) in enumerate(zip(sources, slugs), start=1):
        stem = f"{index:0{number_width}d}-{slug}"
        css_source = find_css_source(source, css_files, used_css, stem)
        css_content = normalize_newline(read_text(css_source)) if css_source is not None else None
        if css_content is None and create_missing_css:
            css_content = f"/* Styles for {stem}.html */\n"
        deck.append(CanonicalSlide(index, slug, f"{stem}.html", f"{stem}.css", source, css_source, css_content))
    return deck


def cmd_move(args: argparse.Namespace) -> None:
    sources = renumbered_sources_from_current_order()
    count = len(sources)
    if args.from_number < 1 or args.from_number > count:
        die(f"FROM must be between 1 and {count}.")
    if args.to_number < 1 or args.to_number > count:
        die(f"TO must be between 1 and {count}.")
    moving = sources.pop(args.from_number - 1)
    sources.insert(args.to_number - 1, moving)

    number_width = detect_number_width([source.path for source in sources], explicit=args.number_width)
    deck = write_temp_sources_for_reorder(sources, number_width, create_missing_css=not args.no_css)
    writes, deletes = plan_rewrite(deck)
    print_plan(f"Move slide {args.from_number} to {args.to_number}", writes, deletes)
    apply_rewrite(writes, deletes, simulate=args.simulate, backup=not args.no_backup)


def cmd_remove(args: argparse.Namespace) -> None:
    sources = renumbered_sources_from_current_order()
    count = len(sources)
    if args.number < 1 or args.number > count:
        die(f"Slide number must be between 1 and {count}.")
    removed = sources.pop(args.number - 1)

    number_width = detect_number_width([source.path for source in sources] or [removed.path], explicit=args.number_width)
    deck = write_temp_sources_for_reorder(sources, number_width, create_missing_css=not args.no_css)
    writes, deletes = plan_rewrite(deck)

    # The removed source naturally appears in deletes. With backup enabled, deletion is safe.
    print_plan(f"Remove slide {args.number}", writes, deletes)
    apply_rewrite(writes, deletes, simulate=args.simulate, backup=not args.no_backup)


def cmd_insert(args: argparse.Namespace) -> None:
    if args.at < 1:
        die("--at must be >= 1")
    if not args.source_html.exists():
        die(f"Input file does not exist: {args.source_html}")

    content = read_text(args.source_html)
    sections = SECTION_RE.findall(content)
    if not sections:
        die(f"No <section> blocks found in {args.source_html}")

    sources = renumbered_sources_from_current_order()
    if args.at > len(sources) + 1:
        die(f"Cannot insert at {args.at}; deck has {len(sources)} slides.")

    inserted: list[SlideSource] = []
    for idx, section in enumerate(sections, start=1):
        slug = slug_from_section(section, fallback=f"inserted-{idx}")
        inserted.append(
            SlideSource(
                path=args.source_html,
                order_number=None,
                order_width=None,
                old_slug=slug,
                content=format_html_fragment(section),
                section=section,
                canonical_slug=slug,
            )
        )

    sources = sources[: args.at - 1] + inserted + sources[args.at - 1 :]
    number_width = detect_number_width([source.path for source in sources], explicit=args.number_width)
    deck = write_temp_sources_for_reorder(sources, number_width, create_missing_css=not args.no_css)
    writes, deletes = plan_rewrite(deck)
    print_plan(f"Insert {len(inserted)} slide(s) at {args.at}", writes, deletes)
    apply_rewrite(writes, deletes, simulate=args.simulate, backup=not args.no_backup)


# ---------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------

def add_common_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--simulate", action="store_true", help="Show planned changes without modifying files.")
    parser.add_argument("--no-backup", action="store_true", help="Do not create a timestamped backup before modifying files.")
    parser.add_argument("--no-css", action="store_true", help="Do not create missing per-slide CSS files.")
    parser.add_argument("--number-width", type=int, choices=[2, 3, 4], help="Override detected filename number width.")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Safely manage Reveal.js slide fragments.")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("format", help="Canonicalize filenames, indentation, CSS files, and manifest. Idempotent.")
    add_common_args(p)
    p.set_defaults(func=cmd_format)

    p = sub.add_parser("check", help="Check whether the project is already canonical.")
    add_common_args(p)
    p.set_defaults(func=cmd_check)

    p = sub.add_parser("sync-imports", help="Rebuild only css/slides.css from canonical slide order.")
    add_common_args(p)
    p.set_defaults(func=cmd_sync_imports)

    p = sub.add_parser("list", help="List canonical slide order and names.")
    add_common_args(p)
    p.set_defaults(func=cmd_list)

    p = sub.add_parser("move", help="Move a slide from one position to another, then canonicalize.")
    p.add_argument("from_number", type=int)
    p.add_argument("to_number", type=int)
    add_common_args(p)
    p.set_defaults(func=cmd_move)

    p = sub.add_parser("remove", help="Remove a slide, then canonicalize.")
    p.add_argument("number", type=int)
    add_common_args(p)
    p.set_defaults(func=cmd_remove)

    p = sub.add_parser("insert", help="Insert section(s) from an HTML file, then canonicalize.")
    p.add_argument("source_html", type=Path)
    p.add_argument("--at", type=int, required=True)
    add_common_args(p)
    p.set_defaults(func=cmd_insert)

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    ensure_dirs()
    args.func(args)


if __name__ == "__main__":
    main()
