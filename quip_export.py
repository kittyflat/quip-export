#!/usr/bin/env python3
"""
Quip to Markdown Exporter
Exports all your Quip documents to local Markdown files,
downloading images alongside and rewriting links to be local.

Usage:
    export QUIP_TOKEN=your_token_here
    python3 quip_export.py --output ~/quip-export

Get your token at: https://quip.com/dev/token
"""

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import urlopen, Request
from urllib.error import URLError, HTTPError


# ---------------------------------------------------------------------------
# Quip API client (no external dependencies)
# ---------------------------------------------------------------------------

CIRCUIT_BREAKER_THRESHOLD = 5   # consecutive final failures before pausing
CIRCUIT_BREAKER_PAUSE     = 180  # seconds to wait when tripped
RETRY_WAITS               = [5, 15, 30]  # seconds between attempts


class QuipClient:
    BASE_URL = "https://platform.quip.com/1"

    def __init__(self, token: str):
        self.token = token
        self._consecutive_failures = 0

    def _get(self, path: str, params: dict = None) -> dict:
        url = f"{self.BASE_URL}/{path}"
        if params:
            query = "&".join(f"{k}={v}" for k, v in params.items())
            url = f"{url}?{query}"
        req = Request(url, headers={"Authorization": f"Bearer {self.token}"})
        for attempt, wait in enumerate(RETRY_WAITS):
            try:
                with urlopen(req, timeout=30) as resp:
                    self._consecutive_failures = 0
                    return json.loads(resp.read().decode())
            except HTTPError as e:
                if e.code in (429, 503) and attempt < len(RETRY_WAITS) - 1:
                    print(f"  [HTTP {e.code}] {path}: {e.reason} — retrying in {wait}s", file=sys.stderr)
                    time.sleep(wait)
                    continue
                print(f"  [HTTP {e.code}] {path}: {e.reason}", file=sys.stderr)
                if e.code in (429, 503):
                    self._consecutive_failures += 1
                    self._maybe_pause()
                return {}
            except URLError as e:
                if attempt < len(RETRY_WAITS) - 1:
                    print(f"  [Network error] {path}: {e.reason} — retrying in {wait}s", file=sys.stderr)
                    time.sleep(wait)
                    continue
                print(f"  [Network error] {path}: {e.reason}", file=sys.stderr)
                self._consecutive_failures += 1
                self._maybe_pause()
                return {}
        return {}

    def _maybe_pause(self):
        if self._consecutive_failures >= CIRCUIT_BREAKER_THRESHOLD:
            print(f"\n  ⚠️  {self._consecutive_failures} consecutive failures — pausing {CIRCUIT_BREAKER_PAUSE}s to let the server recover...", file=sys.stderr)
            time.sleep(CIRCUIT_BREAKER_PAUSE)
            self._consecutive_failures = 0

    def get_authenticated_user(self) -> dict:
        return self._get("users/current")

    def get_folder(self, folder_id: str) -> dict:
        return self._get(f"folders/{folder_id}")

    def get_thread(self, thread_id: str) -> dict:
        return self._get(f"threads/{thread_id}")

    def export_thread_markdown(self, thread_id: str) -> str | None:
        """Export a thread as Markdown via the Quip export endpoint."""
        url = f"{self.BASE_URL}/threads/{thread_id}/export/markdown"
        req = Request(url, headers={"Authorization": f"Bearer {self.token}"})
        for attempt in range(3):
            try:
                with urlopen(req, timeout=60) as resp:
                    return resp.read().decode("utf-8")
            except HTTPError as e:
                # 400/404 = endpoint not supported for this doc type; fall back silently
                if e.code in (400, 404):
                    return None
                if e.code in (429, 503) and attempt < 2:
                    wait = 2 ** (attempt + 1)
                    time.sleep(wait)
                    continue
                print(f"  [HTTP {e.code}] export markdown: {e.reason}", file=sys.stderr)
                return None
            except URLError as e:
                if attempt < 2:
                    time.sleep(2 ** (attempt + 1))
                    continue
                print(f"  [Network error] export markdown: {e.reason}", file=sys.stderr)
                return None
        return None


# ---------------------------------------------------------------------------
# HTML → Markdown fallback (stdlib only)
# ---------------------------------------------------------------------------

def html_to_markdown(html: str) -> str:
    """
    Minimal HTML→Markdown conversion using only stdlib.
    Handles headings, bold, italic, links, images, lists, code, paragraphs.
    """
    import html as html_module
    from html.parser import HTMLParser

    class MDParser(HTMLParser):
        def __init__(self):
            super().__init__()
            self.result = []
            self._stack = []
            self._list_stack = []  # 'ul' or 'ol'
            self._ol_counters = []
            self._skip = False

        def _tag(self):
            return self._stack[-1] if self._stack else None

        def handle_starttag(self, tag, attrs):
            attrs = dict(attrs)
            self._stack.append(tag)
            if tag in ("script", "style"):
                self._skip = True
            elif tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
                level = int(tag[1])
                self.result.append("\n" + "#" * level + " ")
            elif tag == "p":
                self.result.append("\n\n")
            elif tag == "br":
                self.result.append("  \n")
            elif tag == "strong" or tag == "b":
                self.result.append("**")
            elif tag == "em" or tag == "i":
                self.result.append("*")
            elif tag == "code":
                self.result.append("`")
            elif tag == "pre":
                self.result.append("\n```\n")
            elif tag == "a":
                self.result.append("[")
                self._stack[-1] = ("a", attrs.get("href", ""))
            elif tag == "img":
                alt = attrs.get("alt", "image")
                src = attrs.get("src", "")
                self.result.append(f"![{alt}]({src})")
            elif tag == "ul":
                self._list_stack.append("ul")
                self.result.append("\n")
            elif tag == "ol":
                self._list_stack.append("ol")
                self._ol_counters.append(0)
                self.result.append("\n")
            elif tag == "li":
                indent = "  " * (len(self._list_stack) - 1)
                if self._list_stack and self._list_stack[-1] == "ol":
                    self._ol_counters[-1] += 1
                    self.result.append(f"\n{indent}{self._ol_counters[-1]}. ")
                else:
                    self.result.append(f"\n{indent}- ")
            elif tag == "hr":
                self.result.append("\n---\n")
            elif tag == "blockquote":
                self.result.append("\n> ")

        def handle_endtag(self, tag):
            if tag in ("script", "style"):
                self._skip = False
            if not self._stack:
                return
            top = self._stack.pop()
            if tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
                self.result.append("\n")
            elif tag == "strong" or tag == "b":
                self.result.append("**")
            elif tag == "em" or tag == "i":
                self.result.append("*")
            elif tag == "code":
                self.result.append("`")
            elif tag == "pre":
                self.result.append("\n```\n")
            elif tag == "a" and isinstance(top, tuple):
                href = top[1]
                self.result.append(f"]({href})")
            elif tag in ("ul", "ol"):
                if self._list_stack:
                    self._list_stack.pop()
                if tag == "ol" and self._ol_counters:
                    self._ol_counters.pop()
                self.result.append("\n")

        def handle_data(self, data):
            if self._skip:
                return
            self.result.append(html_module.unescape(data))

        def get_markdown(self):
            md = "".join(self.result)
            # Collapse 3+ blank lines to 2
            md = re.sub(r"\n{3,}", "\n\n", md)
            return md.strip()

    parser = MDParser()
    parser.feed(html)
    return parser.get_markdown()


# ---------------------------------------------------------------------------
# Image downloader
# ---------------------------------------------------------------------------

def download_image(url: str, dest_dir: Path, token: str) -> Path | None:
    """Download an image to dest_dir, return relative path or None on failure."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    parsed = urlparse(url)
    filename = Path(parsed.path).name or "image"
    # Sanitize filename
    filename = re.sub(r"[^\w.\-]", "_", filename)
    if not filename or filename == "_":
        filename = f"image_{abs(hash(url)) % 100000}"
    dest = dest_dir / filename

    if dest.exists():
        return dest

    headers = {"Authorization": f"Bearer {token}"}
    req = Request(url, headers=headers)
    try:
        with urlopen(req, timeout=30) as resp:
            dest.write_bytes(resp.read())
        return dest
    except Exception as e:
        print(f"    [warn] Could not download image {url}: {e}", file=sys.stderr)
        return None


def localize_images(markdown: str, images_dir: Path, token: str) -> str:
    """Find all image references in Markdown, download them, rewrite paths."""
    pattern = re.compile(r"!\[([^\]]*)\]\(([^)]+)\)")

    def replace(m):
        alt, url = m.group(1), m.group(2)
        if url.startswith("http"):
            local = download_image(url, images_dir, token)
            if local:
                rel = os.path.relpath(local, images_dir.parent)
                return f"![{alt}]({rel})"
        return m.group(0)

    return pattern.sub(replace, markdown)


# ---------------------------------------------------------------------------
# Folder traversal
# ---------------------------------------------------------------------------

def safe_filename(name: str) -> str:
    """Convert a Quip title to a safe filesystem name."""
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name)
    name = name.strip(". ")
    return name or "untitled"


def export_thread(client: QuipClient, thread_id: str, thread_title: str,
                  dest_dir: Path, token: str, stats: dict):
    filename = safe_filename(thread_title) + ".md"
    dest_file = dest_dir / filename

    if dest_file.exists():
        print(f"  [skip] {filename} (already exported)")
        stats["skipped"] += 1
        return

    print(f"  → {filename}")

    # Try native Markdown export first
    markdown = client.export_thread_markdown(thread_id)

    if not markdown:
        # Fallback: fetch HTML from thread and convert
        thread = client.get_thread(thread_id)
        html = thread.get("html", "")
        if not html:
            print(f"    [warn] No content found for {thread_title}")
            stats["failed"] += 1
            return
        markdown = html_to_markdown(html)

    # Add title as H1 if not already present
    if not markdown.startswith("# "):
        markdown = f"# {thread_title}\n\n{markdown}"

    # Download and localize images
    images_dir = dest_dir / "images"
    markdown = localize_images(markdown, images_dir, token)

    dest_file.write_text(markdown, encoding="utf-8")
    stats["exported"] += 1


def traverse_folder(client: QuipClient, folder_id: str, dest_dir: Path,
                    token: str, stats: dict, depth: int = 0):
    indent = "  " * depth
    folder = client.get_folder(folder_id)
    if not folder:
        return

    folder_title = folder.get("folder", {}).get("title", folder_id)
    print(f"{indent}📁 {folder_title}")

    dest_dir.mkdir(parents=True, exist_ok=True)

    for child in folder.get("children", []):
        thread_id = child.get("thread_id")
        subfolder_id = child.get("folder_id")

        if thread_id:
            thread = client.get_thread(thread_id)
            if not thread:
                continue
            title = thread.get("thread", {}).get("title", thread_id)
            type_ = thread.get("thread", {}).get("type", "")
            if type_ in ("document", "spreadsheet", "slides", ""):
                export_thread(client, thread_id, title, dest_dir, token, stats)
                time.sleep(0.75)  # be polite to the API

        elif subfolder_id:
            sub_folder = client.get_folder(subfolder_id)
            sub_title = sub_folder.get("folder", {}).get("title", subfolder_id)
            sub_dest = dest_dir / safe_filename(sub_title)
            traverse_folder(client, subfolder_id, sub_dest, token, stats, depth + 1)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Export Quip documents to local Markdown files."
    )
    parser.add_argument(
        "--output", "-o",
        default=str(Path.home() / "quip-export"),
        help="Destination folder (default: ~/quip-export)"
    )
    parser.add_argument(
        "--token", "-t",
        default=os.environ.get("QUIP_TOKEN"),
        help="Quip API token (or set QUIP_TOKEN env var)"
    )
    parser.add_argument(
        "--folder", "-f",
        default=None,
        help="Export only a specific folder ID (default: your private folder)"
    )
    args = parser.parse_args()

    if not args.token:
        print("Error: Quip API token required.")
        print("  Get yours at https://quip.com/dev/token")
        print("  Then run: export QUIP_TOKEN=your_token")
        sys.exit(1)

    client = QuipClient(args.token)
    output = Path(args.output).expanduser()

    print(f"\n🔍 Connecting to Quip...")
    user = client.get_authenticated_user()
    if not user:
        print("Error: Could not authenticate. Check your token.")
        sys.exit(1)

    name = user.get("name", "Unknown")
    print(f"✅ Authenticated as: {name}")
    print(f"📂 Exporting to: {output}\n")

    stats = {"exported": 0, "skipped": 0, "failed": 0}

    if args.folder:
        folder_ids = [args.folder]
    else:
        # Export private folder + shared desktop folders
        folder_ids = []
        if user.get("private_folder_id"):
            folder_ids.append(user["private_folder_id"])
        folder_ids.extend(user.get("shared_folder_ids", []))
        folder_ids.extend(user.get("group_folder_ids", []))

    for folder_id in folder_ids:
        traverse_folder(client, folder_id, output, args.token, stats)

    print(f"\n✅ Done!")
    print(f"   Exported : {stats['exported']}")
    print(f"   Skipped  : {stats['skipped']} (already existed)")
    print(f"   Failed   : {stats['failed']}")
    print(f"\nFiles saved to: {output}")


if __name__ == "__main__":
    main()
