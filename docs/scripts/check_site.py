"""Fail when a built documentation page links to a missing local resource."""

from __future__ import annotations

import argparse
import json
import os
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urlsplit


class LinkCollector(HTMLParser):
    """Collect link and resource targets encountered while parsing an HTML page."""

    def __init__(self) -> None:
        """Initialize an empty collection of target URLs."""
        super().__init__()
        self.targets: list[str] = []
        self.anchors: set[str] = set()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        """Record nonblank ``href`` and ``src`` attributes from a start tag."""
        del tag
        for name, value in attrs:
            if name == "id" and value:
                self.anchors.add(value)
            if name in {"href", "src"} and value:
                self.targets.append(value)


def local_target(site: Path, base: str, page: Path, target: str) -> Path | None:
    """Resolve a local page target to the file that should satisfy it.

    Returns:
        The absolute candidate path, or ``None`` for external and fragment links.
    """
    parsed = urlsplit(target)
    if parsed.scheme or parsed.netloc or target.startswith(("#", "mailto:", "tel:")):
        return None
    path = unquote(parsed.path)
    if not path:
        return None
    if path.startswith("/"):
        if path != base and not path.startswith(f"{base}/"):
            return None
        relative = path.removeprefix(base).lstrip("/")
        candidate = site / relative
    else:
        candidate = page.parent / path
    if path.endswith("/") or candidate.is_dir():
        candidate /= "index.html"
    return candidate.resolve()


def broken_links(site: Path, base: str) -> list[str]:
    """Find missing local link targets in every HTML page below a site root.

    Returns:
        Sorted, unique descriptions of pages and their missing targets.
    """
    failures: list[str] = []
    for page in site.rglob("*.html"):
        collector = LinkCollector()
        collector.feed(page.read_text(encoding="utf-8"))
        for target in collector.targets:
            candidate = local_target(site, base, page, target)
            if candidate is not None and not candidate.is_file():
                failures.append(f"{page.relative_to(site)} -> {target}")
    return sorted(set(failures))


def build_parser() -> argparse.ArgumentParser:
    """Build the documentation link-checker command-line parser.

    Returns:
        The parser for the site directory and deployed base path.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--site", type=Path, required=True)
    parser.add_argument("--base", default=os.environ.get("LYRA_DOCS_BASE", "/lyra/dev"))
    return parser


def api_reference_failures(site: Path) -> list[str]:
    """Validate the generated Python API artifacts and every indexed symbol.

    Returns:
        Missing artifacts, pages, and symbol anchors in the built site.
    """
    api = site / "api" / "lyra"
    failures = []
    for name in ("index.html", "symbols.json", "objects.inv", "llms.txt"):
        artifact = api / name
        if not artifact.is_file() or not artifact.stat().st_size:
            failures.append(
                f"Missing or empty API artifact: {artifact.relative_to(site)}"
            )
    index = api / "symbols.json"
    if not index.is_file():
        return failures
    try:
        symbols = json.loads(index.read_text(encoding="utf-8"))["symbols"]
    except (ValueError, KeyError, TypeError):
        return [*failures, "Invalid API symbol index"]
    if not isinstance(symbols, list) or not symbols:
        return [*failures, "API symbol index must contain symbols"]
    return sorted(set(failures + api_symbol_failures(site, symbols)))


def api_symbol_failures(site: Path, symbols: list[object]) -> list[str]:
    """Check indexed symbol destinations and module Markdown exports.

    Returns:
        Invalid entries and missing pages, anchors, or Markdown files.
    """
    api = site / "api" / "lyra"
    failures = []
    pages: dict[Path, LinkCollector] = {}
    for symbol in symbols:
        if not isinstance(symbol, dict) or not all(
            isinstance(symbol.get(key), str) for key in ("page", "anchor", "kind")
        ):
            failures.append("Invalid API symbol entry")
            continue
        page = (site / symbol["page"] / "index.html").resolve()
        if not page.is_relative_to(api.resolve()) or not page.is_file():
            failures.append(f"Missing API symbol page: {symbol['page']}")
            continue
        if page not in pages:
            collector = LinkCollector()
            collector.feed(page.read_text(encoding="utf-8"))
            pages[page] = collector
        anchor = symbol["anchor"]
        if anchor and anchor not in pages[page].anchors:
            failures.append(f"Missing API symbol anchor: {symbol['page']}#{anchor}")
        if symbol["kind"] == "module":
            for suffix in (".md", ".md.txt"):
                markdown = site / f"{symbol['page']}{suffix}"
                if not markdown.is_file():
                    failures.append(
                        f"Missing API Markdown: {markdown.relative_to(site)}"
                    )
    return sorted(set(failures))


def main() -> None:
    """Check the requested documentation tree and exit on broken links.

    Raises:
        SystemExit: If one or more local link targets are missing.
    """
    arguments = build_parser().parse_args()
    failures = broken_links(arguments.site.resolve(), arguments.base.rstrip("/"))
    failures.extend(api_reference_failures(arguments.site.resolve()))
    if failures:
        message = "Broken documentation links:\n" + "\n".join(failures)
        raise SystemExit(message)


if __name__ == "__main__":
    main()
