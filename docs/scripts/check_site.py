"""Fail when a built documentation page links to a missing local resource."""

from __future__ import annotations

import argparse
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urlsplit


class LinkCollector(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.targets: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        del tag
        for name, value in attrs:
            if name in {"href", "src"} and value:
                self.targets.append(value)


def local_target(site: Path, base: str, page: Path, target: str) -> Path | None:
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
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--site", type=Path, required=True)
    parser.add_argument("--base", default="/lyra/dev")
    return parser


def main() -> None:
    arguments = build_parser().parse_args()
    failures = broken_links(arguments.site.resolve(), arguments.base.rstrip("/"))
    if failures:
        message = "Broken documentation links:\n" + "\n".join(failures)
        raise SystemExit(message)


if __name__ == "__main__":
    main()
