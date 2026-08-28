#!/usr/bin/env python3
"""
ScrapeShadow v2 - automated single-page mirror builder
Part of Templar Studios | GPL v3.0

Given a full URL, this tool:
  1. Downloads the raw HTML
  2. Extracts all local assets (scripts, css, images, fonts, favicons)
  3. Downloads each asset preserving folder structure
  4. Rewrites asset paths for local viewing
  5. Saves the visible text and metadata
  6. Zips everything into a single portable archive

One command. Full snapshot.
"""

import sys
import os
import re
import time
import zipfile
import argparse
import hashlib
from urllib import request, error, parse
from urllib.parse import urlparse, urljoin
from html.parser import HTMLParser

USER_AGENT = "ScrapeShadow/2.0 (Templar Studios Open Source)"
TIMEOUT = 20
MAX_HTML_SIZE = 10 * 1024 * 1024
MAX_ASSET_SIZE = 20 * 1024 * 1024


# ------------------------------------------------------------
# HTML parsing and asset extraction
# ------------------------------------------------------------

class AssetExtractor(HTMLParser):
    """Extract all local asset urls from html."""

    def __init__(self, base_url: str) -> None:
        super().__init__()
        self.base_url = base_url
        self.assets = set()
        self.title = None
        self.in_title = False
        self.title_chunks = []

        # tags that carry asset urls
        self.asset_attrs = {
            "script": "src",
            "link": "href",
            "img": "src",
            "image": "src",
            "source": "src",
            "video": "src",
            "audio": "src",
            "iframe": "src",
            "embed": "src",
            "object": "data",
            "use": "href",
        }

        # only grab these link types
        self.link_types = {
            "stylesheet", "icon", "shortcut icon", "preload",
            "apple-touch-icon", "image/png", "image/jpeg",
            "image/webp", "image/svg+xml", "font", "manifest",
        }

    def handle_starttag(self, tag: str, attrs) -> None:
        tag = tag.lower()
        attrs = dict(attrs)

        if tag == "title":
            self.in_title = True
            self.title_chunks = []

        if tag in self.asset_attrs:
            attr_name = self.asset_attrs[tag]
            if attr_name in attrs:
                url = attrs[attr_name].strip()
                if not url:
                    return

                # filter link tags by type
                if tag == "link":
                    rel = attrs.get("rel", "").lower()
                    as_type = attrs.get("as", "").lower()
                    type_attr = attrs.get("type", "").lower()
                    if not (rel in self.link_types or
                            as_type in ("script", "font", "image", "style") or
                            type_attr in self.link_types):
                        return

                full_url = urljoin(self.base_url, url)
                self.assets.add(full_url)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "title":
            self.in_title = False
            self.title = "".join(self.title_chunks).strip()

    def handle_data(self, data: str) -> None:
        if self.in_title:
            self.title_chunks.append(data)


class TextExtractor(HTMLParser):
    """Pull clean visible text from html."""

    def __init__(self) -> None:
        super().__init__()
        self.chunks = []
        self.skip_depth = 0
        self.skip_tags = {"script", "style", "noscript", "iframe", "svg"}

    def handle_starttag(self, tag: str, attrs) -> None:
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


# ------------------------------------------------------------
# Network helpers
# ------------------------------------------------------------

def fetch_bytes(url: str, max_size: int) -> bytes:
    """Download bytes from a url with size cap."""
    req = request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "*/*",
        },
    )
    with request.urlopen(req, timeout=TIMEOUT) as resp:
        data = resp.read(max_size)
        return data


def is_local_asset(url: str, base_domain: str) -> bool:
    """Return true if the asset is on the same domain or relative."""
    parsed = urlparse(url)
    if not parsed.netloc:
        return True
    return parsed.netloc == base_domain


def local_path_from_url(url: str, base_domain: str) -> str:
    """Convert a url to a local file path."""
    parsed = urlparse(url)
    path = parsed.path.lstrip("/")
    if not path:
        # root assets like favicon
        path = "index_asset"
    return path


def rewrite_html(html: str, base_url: str, base_domain: str,
                 asset_map: dict) -> str:
    """Rewrite asset urls to local relative paths."""
    for remote_url, local_path in asset_map.items():
        # replace absolute urls
        html = html.replace(remote_url, local_path)
        # also replace domain-relative urls
        parsed = urlparse(remote_url)
        rel_url = parsed.path
        if parsed.query:
            rel_url += "?" + parsed.query
        html = html.replace(rel_url, local_path)
    return html


# ------------------------------------------------------------
# Main scraper
# ------------------------------------------------------------

def scrape(url: str, verbose: bool = False) -> str:
    """Run the full mirror build. Returns output dir name."""
    parsed_base = urlparse(url)
    base_domain = parsed_base.netloc

    print(f"[*] scraping: {url}")

    # 1. fetch html
    html_bytes = fetch_bytes(url, MAX_HTML_SIZE)
    charset = "utf-8"
    # try to detect charset from meta
    html_head = html_bytes[:2048].decode("utf-8", errors="ignore")
    charset_match = re.search(r'charset=["\']?([a-zA-Z0-9-]+)', html_head)
    if charset_match:
        charset = charset_match.group(1)

    try:
        html = html_bytes.decode(charset, errors="replace")
    except LookupError:
        html = html_bytes.decode("utf-8", errors="replace")

    # 2. extract assets
    extractor = AssetExtractor(url)
    extractor.feed(html)
    extractor.close()

    local_assets = {a for a in extractor.assets if is_local_asset(a, base_domain)}
    if verbose:
        print(f"[*] found {len(local_assets)} local assets")

    # 3. create output dir
    safe_name = base_domain.replace(".", "_")
    out_dir = os.path.join("mirror", safe_name)
    os.makedirs(out_dir, exist_ok=True)

    asset_map = {}

    # 4. download assets
    for i, asset_url in enumerate(sorted(local_assets), 1):
        try:
            asset_bytes = fetch_bytes(asset_url, MAX_ASSET_SIZE)
            local_path = local_path_from_url(asset_url, base_domain)
            full_path = os.path.join(out_dir, local_path)

            # create subdirs
            os.makedirs(os.path.dirname(full_path), exist_ok=True)

            with open(full_path, "wb") as f:
                f.write(asset_bytes)

            asset_map[asset_url] = local_path

            if verbose:
                print(f"[*] ({i}/{len(local_assets)}) saved: {local_path}")
        except Exception as e:
            if verbose:
                print(f"[!] failed to download {asset_url}: {e}")

    # 5. rewrite html
    rewritten = rewrite_html(html, url, base_domain, asset_map)

    html_path = os.path.join(out_dir, "index.html")
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(rewritten)

    # 6. extract text
    text_extractor = TextExtractor()
    text_extractor.feed(html)
    text_extractor.close()
    visible_text = text_extractor.get_text()

    text_path = os.path.join(out_dir, "page.txt")
    with open(text_path, "w", encoding="utf-8") as f:
        f.write(visible_text)

    # 7. metadata
    meta_path = os.path.join(out_dir, "meta.txt")
    with open(meta_path, "w", encoding="utf-8") as f:
        f.write(f"source_url: {url}\n")
        f.write(f"domain: {base_domain}\n")
        f.write(f"title: {extractor.title or 'Untitled'}\n")
        f.write(f"assets_downloaded: {len(asset_map)}\n")
        f.write(f"assets_found: {len(local_assets)}\n")
        f.write(f"saved_at: {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write(f"scraper: ScrapeShadow v2\n")

    # 8. zip archive
    zip_name = os.path.join("mirror", f"{safe_name}.zip")
    with zipfile.ZipFile(zip_name, "w", zipfile.ZIP_DEFLATED) as zf:
        for root, dirs, files in os.walk(out_dir):
            for file in files:
                file_path = os.path.join(root, file)
                arcname = os.path.relpath(file_path, out_dir)
                zf.write(file_path, arcname)

    print(f"\n[+] mirror saved to: {out_dir}/")
    print(f"[+] archive saved to: {zip_name}")
    print(f"[+] assets downloaded: {len(asset_map)}")
    print(f"[+] title: {extractor.title or 'Untitled'}")

    return out_dir


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="scrapeshadow2",
        description="Automated single-page mirror builder by Templar Studios",
    )
    parser.add_argument("url", help="full url to mirror")
    parser.add_argument("-v", "--verbose", action="store_true",
                        help="show detailed download progress")
    args = parser.parse_args()

    try:
        scrape(args.url, args.verbose)
    except error.HTTPError as e:
        print(f"[!] http error {e.code}: {e.reason}", file=sys.stderr)
        sys.exit(1)
    except error.URLError as e:
        print(f"[!] network error: {e.reason}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"[!] error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()