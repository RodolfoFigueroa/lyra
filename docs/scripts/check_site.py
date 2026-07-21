"""Fail when a built documentation page links to a missing local resource."""

from __future__ import annotations

import argparse
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urlsplit


class LinkCollector(HTMLParser):
    """Collect link and resource targets encountered while parsing an HTML page."""

    def __init__(self) -> None:
        """Initialize an empty collection of target URLs."""
        super().__init__()
        self.targets: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        """Record nonblank ``href`` and ``src`` attributes from a start tag."""
        del tag
        for name, value in attrs:
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
    parser.add_argument("--base", default="/lyra/dev")
    return parser


def main() -> None:
    """Check the requested documentation tree and exit on broken links.

    Raises:
        SystemExit: If one or more local link targets are missing.
    """
    arguments = build_parser().parse_args()
    failures = broken_links(arguments.site.resolve(), arguments.base.rstrip("/"))
    if failures:
        message = "Broken documentation links:\n" + "\n".join(failures)
        raise SystemExit(message)


if __name__ == "__main__":
    main()
