#!/usr/bin/env python3
import argparse
import html as html_lib
import json
import os
import re
import shutil
import sys
import urllib.parse
import urllib.request
from pathlib import Path

DEFAULT_DOCS_URL = "https://docs.xendit.co/docs/payments-via-api-overview"
DEFAULT_URL = "auto"


def is_local(path: str) -> bool:
    return path.startswith("file://") or Path(path).exists()


def fetch_url(url: str) -> bytes:
    encoded = urllib.parse.quote(url, safe=":/?=&%")
    req = urllib.request.Request(encoded, headers={"User-Agent": "xendit-cli"})
    with urllib.request.urlopen(req) as resp:
        return resp.read()

def extract_server_state(html: str) -> dict:
    match = re.search(
        r'<script id="serverApp-state" type="application/json">(.*?)</script>',
        html,
        re.DOTALL,
    )
    if not match:
        raise RuntimeError("unable to locate serverApp-state JSON")
    return json.loads(match.group(1))


def collect_postman_strings(data: dict) -> list[str]:
    values: list[str] = []

    def walk(obj):
        if isinstance(obj, dict):
            for v in obj.values():
                walk(v)
        elif isinstance(obj, list):
            for v in obj:
                walk(v)
        elif isinstance(obj, str):
            if "postman_collection" in obj:
                values.append(obj)

    walk(data)
    return values


def extract_postman_candidates(html: str) -> list[str]:
    url_pattern = re.compile(
        r"https://cdn\.document360\.io/[^\"'<>]*postman_collection\.json[^\"'<>]*",
        re.IGNORECASE,
    )
    seen: set[str] = set()
    candidates: list[str] = []

    def add(url: str) -> None:
        cleaned = html_lib.unescape(url).replace("&amp;", "&")
        if cleaned in seen:
            return
        seen.add(cleaned)
        candidates.append(cleaned)

    try:
        state = extract_server_state(html)
    except Exception:
        state = None
    if state:
        for content in collect_postman_strings(state):
            for match in url_pattern.findall(content):
                add(match)

    for match in url_pattern.findall(html):
        add(match)

    if not candidates:
        return []

    signed = [c for c in candidates if "sig=" in c or "sv=" in c]
    unsigned = [c for c in candidates if c not in signed]
    return signed + unsigned


def main() -> int:
    parser = argparse.ArgumentParser(description="Fetch Xendit API spec (Postman/OpenAPI).")
    parser.add_argument("--url", default=os.getenv("XENDIT_SPEC_URL", DEFAULT_URL))
    parser.add_argument("--docs-url", default=os.getenv("XENDIT_DOCS_URL", DEFAULT_DOCS_URL))
    parser.add_argument("--out", default="schemas/xendit.postman_collection.json")
    args = parser.parse_args()

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    if args.url == "auto":
        try:
            html = fetch_url(args.docs_url).decode("utf-8", errors="replace")
        except Exception as exc:
            print(f"error: failed to fetch docs page: {exc}", file=sys.stderr)
            return 1
        candidates = extract_postman_candidates(html)
        if not candidates:
            print(
                "error: unable to find postman_collection.json link in docs page",
                file=sys.stderr,
            )
            return 1
        last_exc: Exception | None = None
        for candidate in candidates:
            try:
                payload = fetch_url(candidate)
            except Exception as exc:
                last_exc = exc
                continue
            out_path.write_bytes(payload)
            print(out_path)
            return 0
        print(f"error: failed to fetch postman collection: {last_exc}", file=sys.stderr)
        return 1

    if is_local(args.url):
        src = args.url.removeprefix("file://")
        shutil.copyfile(src, out_path)
        print(out_path)
        return 0

    try:
        payload = fetch_url(args.url)
    except Exception as exc:
        print(f"error: failed to fetch {args.url}: {exc}", file=sys.stderr)
        return 1

    out_path.write_bytes(payload)
    print(out_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
