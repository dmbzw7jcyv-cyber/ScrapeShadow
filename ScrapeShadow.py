#!/usr/bin/env python3
"""
ScrapeShadow - controlled single-page web scraper
Part of Templar Studios | GPL v3.0

Given a full URL, fetches the page and saves:
    - raw HTML source
    - extracted visible text
    - basic page metadata (title, status code, final URL)

No crawling. No link following. One request per run.
"""

import sys
import os
import argparse
import time
from urllib import request, error, parse
from urllib.parse import urlparse
from html.parser import HTMLParser
from typing import List, Dict, Optional, Tuple

USER_AGENT = "ScrapeShadow/1.0 (Templar Studios Open Source)"
TIMEOUT = 20  # seconds
MAX_SIZE = 10 * 1024 * 1024  # 10 MB hard cap


class TextExtractor(HTMLParser):
    """Pull clean visible text from html, stripping scripts/styles."""

    def __init__(self) -> None:
        super().__init__()
        self.chunks: List[str] = []
        self.skip_depth = 0
        self.skip_tags = {"script", "style", "noscript", "iframe", "svg"}

    def handle_starttag(self, tag: str, attrs: List[Tuple[str, Optional[str]]]) -> None:
        if tag.lower() in self.skip_tags:
            self.skip_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in self.skip_tags and self.skip_depth > 0:
            self.skip_depth -= 1

    def handle_data(self, data: str) -> None:
        if self.skip_depth == 0:
            text = data.strip()
            if text:
                self.chunks.append(text)

    def get_text(self) -> str:
        return "\n\n".join(self.chunks)


def validate_url(raw_url: str) -> str:
    """
    Ensure the url is absolute and has a valid scheme.
    Rejects shorthand like 'example.com' and non-http schemes.
    """
    url = raw_url.strip()

    if not url:
        raise ValueError("empty url")

    parsed = urlparse(url)

    if parsed.scheme not in ("http", "https"):
        raise ValueError(
            "invalid scheme — full url required (http:// or https://)"
        )

    if not parsed.netloc:
        raise ValueError(
            "missing domain — full url required (e.g. https://example.com/page)"
        )

    if parsed.scheme == "http":
        # allow but note it
        pass

    return url


def safe_filename_from_url(url: str) -> str:
    """Create a filesystem-safe base name from the url."""
    parsed = urlparse(url)
    domain = parsed.netloc.replace(":", "_").replace("/", "_")
    path = parsed.path.strip("/").replace("/", "_") or "index"

    # strip weird chars
    keep_chars = ("abcdefghijklmnopqrstuvwxyz"
                  "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
                  "0123456789._-")
    safe_path = "".join(c if c in keep_chars else "_" for c in path)
    safe_domain = "".join(c if c in keep_chars else "_" for c in domain)

    return f"{safe_domain}__{safe_path}"


def fetch_page(url: str) -> Tuple[int, str, str, bytes]:
    """
    Fetch a single url.
    Returns (status_code, final_url, content_type, body_bytes)
    """
    req = request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,text/plain;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.7",
        },
    )

    with request.urlopen(req, timeout=TIMEOUT) as resp:
        status = getattr(resp, "status", 200)
        final_url = resp.geturl()
        content_type = resp.headers.get("Content-Type", "text/html")
        body = resp.read(MAX_SIZE)
        return status, final_url, content_type, body


def extract_title(html: str) -> Optional[str]:
    """Pull <title> text from html if present."""
    lower = html.lower()
    start = lower.find("<title")
    if start == -1:
        return None

    gt = lower.find(">", start)
    end = lower.find("</title>", gt)
    if gt == -1 or end == -1:
        return None

    return html[gt+1:end].strip()


def save_outputs(base_name: str, url: str, status: int,
                  final_url: str, content_type: str,
                  html_text: str, visible_text: str) -> None:
    """Write all artifacts to local files."""
    out_dir = "scrapes"
    os.makedirs(out_dir, exist_ok=True)

    # raw html
    html_path = os.path.join(out_dir, f"{base_name}.html")
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(html_text)

    # visible text
    text_path = os.path.join(out_dir, f"{base_name}.txt")
    with open(text_path, "w", encoding="utf-8") as f:
        f.write(visible_text)

    # metadata
    meta_path = os.path.join(out_dir, f"{base_name}.meta.txt")
    with open(meta_path, "w", encoding="utf-8") as f:
        f.write(f"source_url: {url}\n")
        f.write(f"final_url:  {final_url}\n")
        f.write(f"http_status: {status}\n")
        f.write(f"content_type: {content_type}\n")
        f.write(f"saved_at:  {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write(f"scraper:   ScrapeShadow 1.0\n")

    print(f"\n[+] saved to ./{out_dir}/")
    print(f"    ├─ {base_name}.html")
    print(f"    ├─ {base_name}.txt")
    print(f"    └─ {base_name}.meta.txt")


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="scrapeshadow",
        description="Controlled single-page web scraper by Templar Studios",
    )
    parser.add_argument(
        "url",
        help="full url to scrape (http:// or https:// required)",
    )
    parser.add_argument(
        "--no-html",
        action="store_true",
        help="skip saving raw html, keep only visible text",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="show network and parsing details",
    )

    args = parser.parse_args()

    try:
        clean_url = validate_url(args.url)
    except ValueError as e:
        print(f"[!] {e}", file=sys.stderr)
        sys.exit(1)

    print(f"[*] scraping: {clean_url}")

    try:
        status, final_url, content_type, body = fetch_page(clean_url)
    except error.HTTPError as e:
        print(f"[!] http error {e.code}: {e.reason}", file=sys.stderr)
        sys.exit(1)
    except error.URLError as e:
        print(f"[!] network error: {e.reason}", file=sys.stderr)
        sys.exit(1)
    except TimeoutError:
        print(f"[!] request timed out after {TIMEOUT} seconds", file=sys.stderr)
        sys.exit(1)

    if args.verbose:
        print(f"[*] http status: {status}")
        print(f"[*] final url:  {final_url}")
        print(f"[*] content type: {content_type}")
        print(f"[*] body size:  {len(body):,} bytes")

    # decode body
    charset = "utf-8"
    if "charset=" in content_type:
        try:
            charset = content_type.split("charset=")[1].split(";")[0].strip()
        except IndexError:
            charset = "utf-8"

    try:
        html_text = body.decode(charset, errors="replace")
    except LookupError:
        html_text = body.decode("utf-8", errors="replace")

    # extract visible text
    extractor = TextExtractor()
    extractor.feed(html_text)
    visible_text = extractor.get_text()

    if not visible_text.strip():
        visible_text = "(no visible text extracted — page may be js-rendered)"

    # optional title
    title = extract_title(html_text)
    if title:
        print(f"[*] title: {title}")

    # write files
    base_name = safe_filename_from_url(clean_url)
    if args.no_html:
        # only txt + meta
        out_dir = "scrapes"
        os.makedirs(out_dir, exist_ok=True)

        text_path = os.path.join(out_dir, f"{base_name}.txt")
        with open(text_path, "w", encoding="utf-8") as f:
            f.write(visible_text)

        meta_path = os.path.join(out_dir, f"{base_name}.meta.txt")
        with open(meta_path, "w", encoding="utf-8") as f:
            f.write(f"source_url: {clean_url}\n")
            f.write(f"final_url:  {final_url}\n")
            f.write(f"http_status: {status}\n")
            f.write(f"content_type: {content_type}\n")
            f.write(f"saved_at:  {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
            f.write(f"scraper:   ScrapeShadow 1.0\n")

        print(f"\n[+] saved to ./{out_dir}/")
        print(f"    ├─ {base_name}.txt")
        print(f"    └─ {base_name}.meta.txt")
    else:
        save_outputs(base_name, clean_url, status, final_url,
                     content_type, html_text, visible_text)

    print("\ndone.")


if __name__ == "__main__":
    main()
