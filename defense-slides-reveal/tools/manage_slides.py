#!/usr/bin/env python3
"""
manage_slides.py

Utility for managing Reveal.js slide fragments stored as separate HTML files.

Expected structure:

  slides/
    001-title.html
    002-outline.html
    ...

  css/
    slides.css
    slides/
      001-title.css
      002-outline.css
      ...

The build script can remain simple: it only concatenates slide HTML files
in lexicographical order.

This tool handles:
  - inserting slides from an HTML file containing one or more <section> blocks
  - removing slides
  - moving slides
  - renumbering slides
  - formatting slide HTML indentation
  - checking consistency
  - syncing css/slides.css imports

All mutating commands support --simulate.
"""

from __future__ import annotations

import argparse
import html
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional


# ---------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------

PROJECT_ROOT = Path.cwd()

SLIDES_DIR = PROJECT_ROOT / "slides"
CSS_DIR = PROJECT_ROOT / "css"
CSS_SLIDES_DIR = CSS_DIR / "slides"
CSS_MANIFEST = CSS_DIR / "slides.css"

print("Slides dir : ", SLIDES_DIR)
print("css dir : ", CSS_DIR)
print("css manifest : ", CSS_MANIFEST)

IMPORT_BEGIN = "/* BEGIN AUTO SLIDE IMPORTS */"
IMPORT_END = "/* END AUTO SLIDE IMPORTS */"

NUMBER_WIDTH = 3

SECTION_RE = re.compile(
    r"<section\b[^>]*>.*?</section>",
    flags=re.IGNORECASE | re.DOTALL,
)

IMPORT_RE = re.compile(
    r"""@import\s+(?:url\()?["'](?P<path>./slides/[^"')]+\.css)["']\)?\s*;"""
)

VOID_TAGS = {
    "area",
    "base",
    "br",
    "col",
    "embed",
    "hr",
    "img",
    "input",
    "link",
    "meta",
    "param",
    "source",
    "track",
    "wbr",
}

RAW_TEXT_TAGS = {
    "script",
    "style",
    "pre",
    "code",
    "textarea",
}


# ---------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------

@dataclass(frozen=True)
class SlideEntry:
    number: int
    slug: str
    html_path: Path
    css_path: Optional[Path]

    @property
    def basename(self) -> str:
        return f"{self.number:0{NUMBER_WIDTH}d}-{self.slug}"

    @property
    def html_name(self) -> str:
        return f"{self.basename}.html"

    @property
    def css_name(self) -> str:
        return f"{self.basename}.css"


@dataclass(frozen=True)
class FileMove:
    src: Path
    dst: Path


@dataclass(frozen=True)
class FileWrite:
    path: Path
    content: str


@dataclass(frozen=True)
class FileDelete:
    path: Path


@dataclass(frozen=True)
class PlannedSlide:
    slug: str
    html_source_path: Optional[Path]
    html_content: Optional[str]
    css_source_path: Optional[Path]
    css_content: Optional[str]


# ---------------------------------------------------------------------
# Utility helpers
# ---------------------------------------------------------------------

def die(message: str, code: int = 1) -> None:
    print(f"ERROR: {message}", file=sys.stderr)
    raise SystemExit(code)


def ensure_dirs() -> None:
    SLIDES_DIR.mkdir(parents=True, exist_ok=True)
    CSS_SLIDES_DIR.mkdir(parents=True, exist_ok=True)


def parse_slide_filename(path: Path) -> Optional[tuple[int, str]]:
    match = re.match(rf"^(\d{{{NUMBER_WIDTH}}})-(.+)\.html$", path.name)
    if not match:
        return None
    return int(match.group(1)), match.group(2)


def parse_css_filename(path: Path) -> Optional[tuple[int, str]]:
    match = re.match(rf"^(\d{{{NUMBER_WIDTH}}})-(.+)\.css$", path.name)
    if not match:
        return None
    return int(match.group(1)), match.group(2)


def numbered_name(number: int, slug: str, suffix: str) -> str:
    return f"{number:0{NUMBER_WIDTH}d}-{slug}{suffix}"


def extract_text_from_html(fragment: str) -> str:
    text = re.sub(r"<script\b[^>]*>.*?</script>", " ", fragment, flags=re.I | re.S)
    text = re.sub(r"<style\b[^>]*>.*?</style>", " ", text, flags=re.I | re.S)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def first_heading_text(section: str) -> Optional[str]:
    match = re.search(
        r"<h[1-3]\b[^>]*>(.*?)</h[1-3]>",
        section,
        flags=re.I | re.S,
    )
    if not match:
        return None
    return extract_text_from_html(match.group(1))


def data_title_text(section: str) -> Optional[str]:
    match = re.search(
        r"<section\b[^>]*\bdata-title=[\"']([^\"']+)[\"']",
        section,
        flags=re.I | re.S,
    )
    if not match:
        return None
    return html.unescape(match.group(1)).strip()


def slugify(text: str, fallback: str = "slide", max_words: int = 8) -> str:
    text = re.sub(r"\$.*?\$", " ", text)
    text = re.sub(r"\\\w+(?:\{[^}]*\})?", " ", text)
    text = html.unescape(text)
    text = text.lower()
    text = re.sub(r"[^a-z0-9]+", " ", text)
    words = [word for word in text.split() if word]

    if not words:
        return fallback

    return "-".join(words[:max_words])


def slug_from_section(section: str, fallback_index: int) -> str:
    title = data_title_text(section)

    if not title:
        title = first_heading_text(section)

    if not title:
        title = extract_text_from_html(section)

    return slugify(title, fallback=f"slide-{fallback_index}")


def unique_slugs(slugs: Iterable[str]) -> list[str]:
    seen: dict[str, int] = {}
    result: list[str] = []

    for slug in slugs:
        base = slug
        count = seen.get(base, 0)

        if count == 0:
            result.append(base)
        else:
            result.append(f"{base}-{count + 1}")

        seen[base] = count + 1

    return result


def make_text_write(path: Path, content: str) -> FileWrite:
    if not content.endswith("\n"):
        content += "\n"
    return FileWrite(path, content)


def make_html_write(path: Path, content: str) -> FileWrite:
    return FileWrite(path, format_html_fragment(content))


# ---------------------------------------------------------------------
# HTML formatting
# ---------------------------------------------------------------------

def format_html_fragment(source: str, indent: str = "  ") -> str:
    """
    Lightweight formatter for Reveal.js slide fragments.

    This is intentionally conservative:
      - preserves content inside raw text tags reasonably well
      - indents nested tags by two spaces
      - returns a trailing newline

    It is not a full browser-grade HTML formatter, but it is suitable for
    predictable Reveal.js slide fragments.
    """
    source = source.strip()

    tokens = re.findall(
        r"<!--.*?-->|<!\[CDATA\[.*?\]\]>|</?[^>]+>|[^<]+",
        source,
        flags=re.S,
    )

    lines: list[str] = []
    level = 0
    raw_stack: list[str] = []

    def current_indent() -> str:
        return indent * max(level, 0)

    for token in tokens:
        if not token:
            continue

        stripped = token.strip()

        if not stripped:
            continue

        if raw_stack:
            closing_raw = re.match(
                rf"</{re.escape(raw_stack[-1])}\s*>",
                stripped,
                flags=re.I,
            )

            if closing_raw:
                level -= 1
                lines.append(current_indent() + stripped)
                raw_stack.pop()
            else:
                raw_lines = token.strip("\n").splitlines()
                for raw_line in raw_lines:
                    if raw_line.strip():
                        lines.append(current_indent() + raw_line.rstrip())
            continue

        close_match = re.match(r"</([a-zA-Z0-9:-]+)\s*>", stripped)
        if close_match:
            level -= 1
            lines.append(current_indent() + stripped)
            continue

        open_match = re.match(r"<([a-zA-Z0-9:-]+)(\s|>|/)", stripped)
        if open_match:
            tag = open_match.group(1).lower()
            is_comment = stripped.startswith("<!--")
            is_doctype = stripped.startswith("<!")
            is_inline_closed = re.search(rf"</{re.escape(tag)}\s*>", stripped, flags=re.I)
            is_self_closing = (
                stripped.endswith("/>")
                or tag in VOID_TAGS
                or is_comment
                or is_doctype
                or bool(is_inline_closed)
            )

            lines.append(current_indent() + stripped)

            if not is_self_closing:
                level += 1
                if tag in RAW_TEXT_TAGS:
                    raw_stack.append(tag)

            continue

        text = re.sub(r"\s+", " ", stripped)
        if text:
            lines.append(current_indent() + text)

    formatted = "\n".join(line.rstrip() for line in lines if line.strip())
    return formatted + "\n"


def html_needs_formatting(path: Path) -> bool:
    original = path.read_text(encoding="utf-8")
    formatted = format_html_fragment(original)
    return original != formatted


# ---------------------------------------------------------------------
# Slide discovery
# ---------------------------------------------------------------------

def read_slide_entries() -> list[SlideEntry]:
    if not SLIDES_DIR.exists():
        return []

    entries: list[SlideEntry] = []

    for html_path in sorted(SLIDES_DIR.glob("*.html")):
        parsed = parse_slide_filename(html_path)

        if parsed is None:
            continue

        number, slug = parsed
        css_path = CSS_SLIDES_DIR / f"{number:0{NUMBER_WIDTH}d}-{slug}.css"

        entries.append(
            SlideEntry(
                number=number,
                slug=slug,
                html_path=html_path,
                css_path=css_path if css_path.exists() else None,
            )
        )

    return sorted(entries, key=lambda entry: entry.number)


def current_planned_slides() -> list[PlannedSlide]:
    entries = read_slide_entries()
    planned: list[PlannedSlide] = []

    for entry in entries:
        planned.append(
            PlannedSlide(
                slug=entry.slug,
                html_source_path=entry.html_path,
                html_content=None,
                css_source_path=entry.css_path,
                css_content=None,
            )
        )

    return planned


# ---------------------------------------------------------------------
# Section extraction
# ---------------------------------------------------------------------

def extract_sections(source_html: Path) -> list[str]:
    if not source_html.exists():
        die(f"Input HTML file does not exist: {source_html}")

    content = source_html.read_text(encoding="utf-8")
    sections = SECTION_RE.findall(content)

    if not sections:
        die(f"No <section>...</section> blocks found in {source_html}")

    return [format_html_fragment(section) for section in sections]


# ---------------------------------------------------------------------
# CSS manifest handling
# ---------------------------------------------------------------------

def import_line_for_css(css_name: str) -> str:
    return f'@import "./slides/{css_name}";'


def expected_import_lines_from_files() -> list[str]:
    css_files = sorted(CSS_SLIDES_DIR.glob("*.css"))
    return [import_line_for_css(path.name) for path in css_files]


def read_manifest() -> str:
    if not CSS_MANIFEST.exists():
        return f"{IMPORT_BEGIN}\n{IMPORT_END}\n"
    return CSS_MANIFEST.read_text(encoding="utf-8")


def replace_managed_import_block(content: str, import_lines: list[str]) -> str:
    block = IMPORT_BEGIN + "\n"

    if import_lines:
        block += "\n".join(import_lines) + "\n"

    block += IMPORT_END

    if IMPORT_BEGIN in content and IMPORT_END in content:
        pattern = re.compile(
            re.escape(IMPORT_BEGIN) + r".*?" + re.escape(IMPORT_END),
            flags=re.S,
        )
        return pattern.sub(block, content)

    prefix = block + "\n\n"
    return prefix + content.strip() + "\n"


def sync_imports_plan() -> list[FileWrite]:
    content = read_manifest()
    synced = replace_managed_import_block(content, expected_import_lines_from_files())

    if synced == content:
        return []

    return [make_text_write(CSS_MANIFEST, synced)]


def imports_in_manifest() -> list[str]:
    content = read_manifest()
    return [match.group("path") for match in IMPORT_RE.finditer(content)]


# ---------------------------------------------------------------------
# Transaction handling
# ---------------------------------------------------------------------

def print_plan(
    title: str,
    moves: Optional[list[FileMove]] = None,
    writes: Optional[list[FileWrite]] = None,
    deletes: Optional[list[FileDelete]] = None,
) -> None:
    moves = moves or []
    writes = writes or []
    deletes = deletes or []

    print(f"\n{title}")

    if not moves and not writes and not deletes:
        print("  No changes.")
        return

    if moves:
        print("\n  Moves:")
        for move in moves:
            print(f"    {move.src} -> {move.dst}")

    if writes:
        print("\n  Writes:")
        for write in writes:
            print(f"    {write.path}")

    if deletes:
        print("\n  Deletes:")
        for delete in deletes:
            print(f"    {delete.path}")


def apply_transaction(
    moves: list[FileMove],
    writes: list[FileWrite],
    deletes: list[FileDelete],
    simulate: bool,
) -> None:
    if simulate:
        return

    ensure_dirs()

    move_dsts = {move.dst.resolve() for move in moves}

    for delete in deletes:
        if delete.path.resolve() in move_dsts:
            continue

        if delete.path.exists():
            delete.path.unlink()

    temp_moves: list[FileMove] = []
    final_moves: list[FileMove] = []

    for index, move in enumerate(moves):
        if not move.src.exists():
            continue

        if move.src.resolve() == move.dst.resolve():
            continue

        temp = move.src.with_name(f".tmp-slide-manager-{index}-{move.src.name}")
        temp_moves.append(FileMove(move.src, temp))
        final_moves.append(FileMove(temp, move.dst))

    for move in temp_moves:
        move.dst.parent.mkdir(parents=True, exist_ok=True)
        move.src.rename(move.dst)

    for move in final_moves:
        move.dst.parent.mkdir(parents=True, exist_ok=True)
        move.dst.unlink(missing_ok=True)
        move.src.rename(move.dst)

    for write in writes:
        write.path.parent.mkdir(parents=True, exist_ok=True)
        write.path.write_text(write.content, encoding="utf-8")


# ---------------------------------------------------------------------
# Rebuild planning
# ---------------------------------------------------------------------

def plan_rebuild_from_order(
    planned: list[PlannedSlide],
    create_missing_css: bool = True,
) -> tuple[list[FileMove], list[FileWrite], list[FileDelete]]:
    existing_html = set(SLIDES_DIR.glob("*.html")) if SLIDES_DIR.exists() else set()
    existing_css = set(CSS_SLIDES_DIR.glob("*.css")) if CSS_SLIDES_DIR.exists() else set()

    moves: list[FileMove] = []
    writes: list[FileWrite] = []

    desired_html: set[Path] = set()
    desired_css: set[Path] = set()

    used_slugs = unique_slugs([item.slug for item in planned])

    for index, item in enumerate(planned, start=1):
        slug = used_slugs[index - 1]

        html_dst = SLIDES_DIR / numbered_name(index, slug, ".html")
        css_dst = CSS_SLIDES_DIR / numbered_name(index, slug, ".css")

        desired_html.add(html_dst)

        if item.html_source_path is not None:
            moves.append(FileMove(item.html_source_path, html_dst))
        elif item.html_content is not None:
            writes.append(make_html_write(html_dst, item.html_content))
        else:
            die("Internal error: planned slide has no HTML source or content.")

        if item.css_source_path is not None:
            desired_css.add(css_dst)
            moves.append(FileMove(item.css_source_path, css_dst))
        elif item.css_content is not None:
            desired_css.add(css_dst)
            writes.append(make_text_write(css_dst, item.css_content))
        elif create_missing_css:
            desired_css.add(css_dst)
            writes.append(make_text_write(css_dst, f"/* Styles for {html_dst.name} */"))

    deletes: list[FileDelete] = []

    for path in sorted(existing_html):
        if path not in desired_html:
            deletes.append(FileDelete(path))

    for path in sorted(existing_css):
        if path not in desired_css:
            deletes.append(FileDelete(path))

    return moves, writes, deletes


# ---------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------

def cmd_list(args: argparse.Namespace) -> None:
    entries = read_slide_entries()

    if not entries:
        print("No slide files found.")
        return

    for entry in entries:
        css_status = "css" if entry.css_path else "missing-css"
        print(f"{entry.number:0{NUMBER_WIDTH}d}  {entry.slug}  [{css_status}]")


def cmd_check(args: argparse.Namespace) -> None:
    errors: list[str] = []
    warnings: list[str] = []
    format_writes: list[FileWrite] = []

    html_files = sorted(SLIDES_DIR.glob("*.html")) if SLIDES_DIR.exists() else []
    css_files = sorted(CSS_SLIDES_DIR.glob("*.css")) if CSS_SLIDES_DIR.exists() else []

    parsed_html: list[tuple[int, str, Path]] = []

    for path in html_files:
        parsed = parse_slide_filename(path)

        if parsed is None:
            errors.append(f"Invalid slide filename: {path}")
            continue

        number, slug = parsed
        parsed_html.append((number, slug, path))

        content = path.read_text(encoding="utf-8")
        sections = SECTION_RE.findall(content)

        if len(sections) == 0:
            errors.append(f"{path} contains no <section> block.")
        elif len(sections) > 1:
            errors.append(f"{path} contains {len(sections)} <section> blocks.")

        formatted = format_html_fragment(content)
        if content != formatted:
            message = f"Bad indentation or formatting in {path}."
            if args.simulate:
                warnings.append(message)
                format_writes.append(FileWrite(path, formatted))
            else:
                errors.append(message + " Run: python tools/manage_slides.py format")

        expected_css = CSS_SLIDES_DIR / f"{number:0{NUMBER_WIDTH}d}-{slug}.css"
        if not expected_css.exists():
            message = f"Missing matching CSS file for {path}: expected {expected_css}"
            if args.allow_missing_css:
                warnings.append(message)
            else:
                errors.append(message)

    numbers = [number for number, _, _ in parsed_html]

    for number in sorted(set(numbers)):
        if numbers.count(number) > 1:
            errors.append(f"Duplicate slide number: {number:0{NUMBER_WIDTH}d}")

    if numbers:
        expected_numbers = list(range(1, max(numbers) + 1))
        missing_numbers = sorted(set(expected_numbers) - set(numbers))

        for number in missing_numbers:
            errors.append(f"Missing slide number: {number:0{NUMBER_WIDTH}d}")

    html_basenames = {path.stem for _, _, path in parsed_html}

    for css_path in css_files:
        parsed = parse_css_filename(css_path)

        if parsed is None:
            warnings.append(f"Invalid slide CSS filename: {css_path}")
            continue

        if css_path.stem not in html_basenames:
            warnings.append(f"Orphan CSS file: {css_path}")

    actual_imports = imports_in_manifest()
    expected_imports = [f"./slides/{path.name}" for path in css_files]

    actual_set = set(actual_imports)
    expected_set = set(expected_imports)

    for missing_import in sorted(expected_set - actual_set):
        errors.append(
            f"Missing import in {CSS_MANIFEST}: "
            f'@import "{missing_import}";'
        )

    for stale_import in sorted(actual_set - expected_set):
        errors.append(
            f"Stale import in {CSS_MANIFEST}: "
            f'@import "{stale_import}";'
        )

    if actual_imports != expected_imports:
        errors.append(f"Imports in {CSS_MANIFEST} are not synchronized or not in slide order.")

    if args.simulate and format_writes:
        print_plan("Formatting changes that would be needed", writes=format_writes)

    if warnings:
        print("\nWarnings:")
        for warning in warnings:
            print(f"  - {warning}")

    if errors:
        print("\nErrors:")
        for error in errors:
            print(f"  - {error}")
        raise SystemExit(1)

    print("Slide structure looks good.")


def cmd_format(args: argparse.Namespace) -> None:
    writes: list[FileWrite] = []

    html_files = sorted(SLIDES_DIR.glob("*.html")) if SLIDES_DIR.exists() else []

    for path in html_files:
        original = path.read_text(encoding="utf-8")
        formatted = format_html_fragment(original)

        if original != formatted:
            writes.append(FileWrite(path, formatted))

    writes.extend(sync_imports_plan())

    print_plan("Format slide files", writes=writes)
    apply_transaction([], writes, [], simulate=args.simulate)


def cmd_sync_imports(args: argparse.Namespace) -> None:
    writes = sync_imports_plan()

    print_plan("Sync CSS imports", writes=writes)
    apply_transaction([], writes, [], simulate=args.simulate)


def cmd_renumber(args: argparse.Namespace) -> None:
    planned = current_planned_slides()

    moves, writes, deletes = plan_rebuild_from_order(
        planned,
        create_missing_css=not args.no_css,
    )

    print_plan("Renumber slides", moves=moves, writes=writes, deletes=deletes)

    if not args.simulate:
        apply_transaction(moves, writes, deletes, simulate=False)
        sync_writes = sync_imports_plan()
        apply_transaction([], sync_writes, [], simulate=False)
    else:
        print("\n  Then regenerate css/slides.css imports.")


def cmd_insert(args: argparse.Namespace) -> None:
    at = args.at

    if at < 1:
        die("--at must be >= 1")

    sections = extract_sections(args.source_html)
    slugs = unique_slugs(
        slug_from_section(section, index)
        for index, section in enumerate(sections, start=1)
    )

    planned = current_planned_slides()

    if at > len(planned) + 1:
        die(f"Cannot insert at {at}; current deck has {len(planned)} slides.")

    new_items = [
        PlannedSlide(
            slug=slug,
            html_source_path=None,
            html_content=section,
            css_source_path=None,
            css_content=None if args.no_css else f"/* Styles for inserted slide: {slug} */",
        )
        for slug, section in zip(slugs, sections)
    ]

    updated = planned[: at - 1] + new_items + planned[at - 1 :]

    moves, writes, deletes = plan_rebuild_from_order(
        updated,
        create_missing_css=not args.no_css,
    )

    print_plan("Insert slides", moves=moves, writes=writes, deletes=deletes)

    if not args.simulate:
        apply_transaction(moves, writes, deletes, simulate=False)
        sync_writes = sync_imports_plan()
        apply_transaction([], sync_writes, [], simulate=False)
    else:
        print("\n  Then regenerate css/slides.css imports.")


def cmd_remove(args: argparse.Namespace) -> None:
    number = args.number

    planned = current_planned_slides()

    if number < 1 or number > len(planned):
        die(f"Cannot remove slide {number}; current deck has {len(planned)} slides.")

    removed = planned[number - 1]
    updated = planned[: number - 1] + planned[number:]

    moves, writes, deletes = plan_rebuild_from_order(
        updated,
        create_missing_css=not args.no_css,
    )

    if not args.delete:
        archive_dir = PROJECT_ROOT / "deleted_slides"
        archive_css_dir = archive_dir / "css"

        removed_html = removed.html_source_path
        removed_css = removed.css_source_path

        if removed_html is not None and removed_html.exists():
            moves.append(FileMove(removed_html, archive_dir / removed_html.name))

        if removed_css is not None and removed_css.exists():
            moves.append(FileMove(removed_css, archive_css_dir / removed_css.name))

        archive_paths = {
            removed_html,
            removed_css,
        }

        deletes = [
            delete
            for delete in deletes
            if delete.path not in archive_paths
        ]

    print_plan("Remove slide", moves=moves, writes=writes, deletes=deletes)

    if not args.simulate:
        apply_transaction(moves, writes, deletes, simulate=False)
        sync_writes = sync_imports_plan()
        apply_transaction([], sync_writes, [], simulate=False)
    else:
        print("\n  Then regenerate css/slides.css imports.")


def cmd_move(args: argparse.Namespace) -> None:
    from_number = args.from_number
    to_number = args.to_number

    planned = current_planned_slides()
    count = len(planned)

    if from_number < 1 or from_number > count:
        die(f"FROM must be between 1 and {count}.")

    if to_number < 1 or to_number > count:
        die(f"TO must be between 1 and {count}.")

    if from_number == to_number:
        print("Source and destination are the same. No changes.")
        return

    moving = planned[from_number - 1]
    remaining = planned[: from_number - 1] + planned[from_number:]

    updated = remaining[: to_number - 1] + [moving] + remaining[to_number - 1:]

    moves, writes, deletes = plan_rebuild_from_order(
        updated,
        create_missing_css=not args.no_css,
    )

    print_plan(
        f"Move slide {from_number:0{NUMBER_WIDTH}d} to {to_number:0{NUMBER_WIDTH}d}",
        moves=moves,
        writes=writes,
        deletes=deletes,
    )

    if not args.simulate:
        apply_transaction(moves, writes, deletes, simulate=False)
        sync_writes = sync_imports_plan()
        apply_transaction([], sync_writes, [], simulate=False)
    else:
        print("\n  Then regenerate css/slides.css imports.")


# ---------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------

def add_simulate_arg(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--simulate",
        action="store_true",
        help="Show planned changes without modifying files.",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Manage Reveal.js slide ordering and slide CSS imports."
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    p_list = subparsers.add_parser(
        "list",
        help="List ordered slides.",
    )
    p_list.set_defaults(func=cmd_list)

    p_check = subparsers.add_parser(
        "check",
        help="Check slide/CSS consistency.",
    )
    p_check.add_argument(
        "--allow-missing-css",
        action="store_true",
        help="Treat missing per-slide CSS files as warnings instead of errors.",
    )
    add_simulate_arg(p_check)
    p_check.set_defaults(func=cmd_check)

    p_format = subparsers.add_parser(
        "format",
        help="Format slide HTML files and synchronize css/slides.css imports.",
    )
    add_simulate_arg(p_format)
    p_format.set_defaults(func=cmd_format)

    p_sync = subparsers.add_parser(
        "sync-imports",
        help="Regenerate the managed import block in css/slides.css.",
    )
    add_simulate_arg(p_sync)
    p_sync.set_defaults(func=cmd_sync_imports)

    p_renumber = subparsers.add_parser(
        "renumber",
        help="Renumber all slides based on current lexicographical order.",
    )
    p_renumber.add_argument(
        "--no-css",
        action="store_true",
        help="Do not create missing CSS files during renumbering.",
    )
    add_simulate_arg(p_renumber)
    p_renumber.set_defaults(func=cmd_renumber)

    p_insert = subparsers.add_parser(
        "insert",
        help="Extract <section> blocks from an HTML file and insert them.",
    )
    p_insert.add_argument("source_html", type=Path)
    p_insert.add_argument("--at", type=int, required=True)
    p_insert.add_argument(
        "--no-css",
        action="store_true",
        help="Do not create CSS files for inserted slides.",
    )
    add_simulate_arg(p_insert)
    p_insert.set_defaults(func=cmd_insert)

    p_remove = subparsers.add_parser(
        "remove",
        help="Remove one slide and shift following slides down.",
    )
    p_remove.add_argument("number", type=int)
    p_remove.add_argument(
        "--delete",
        action="store_true",
        help="Permanently delete removed slide files instead of archiving them.",
    )
    p_remove.add_argument(
        "--no-css",
        action="store_true",
        help="Do not create missing CSS files while rebuilding.",
    )
    add_simulate_arg(p_remove)
    p_remove.set_defaults(func=cmd_remove)

    p_move = subparsers.add_parser(
        "move",
        help="Move a slide from one position to another.",
    )
    p_move.add_argument("from_number", type=int)
    p_move.add_argument("to_number", type=int)
    p_move.add_argument(
        "--no-css",
        action="store_true",
        help="Do not create missing CSS files while rebuilding.",
    )
    add_simulate_arg(p_move)
    p_move.set_defaults(func=cmd_move)

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    ensure_dirs()

    args.func(args)


if __name__ == "__main__":
    main()
