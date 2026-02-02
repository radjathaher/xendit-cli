#!/usr/bin/env python3
import argparse
import os
import shutil
import sys
import urllib.parse
import urllib.request
from pathlib import Path

DEFAULT_URL = "https://cdn.document360.io/217abc43-8677-41fb-a81d-fceeb1fa0358/Images/Documentation/Payments API v3.postman_collection.json"


def is_local(path: str) -> bool:
    return path.startswith("file://") or Path(path).exists()


def fetch_url(url: str) -> bytes:
    encoded = urllib.parse.quote(url, safe=":/?=&%")
    req = urllib.request.Request(encoded, headers={"User-Agent": "xendit-cli"})
    with urllib.request.urlopen(req) as resp:
        return resp.read()


def main() -> int:
    parser = argparse.ArgumentParser(description="Fetch Xendit API spec (Postman/OpenAPI).")
    parser.add_argument("--url", default=os.getenv("XENDIT_SPEC_URL", DEFAULT_URL))
    parser.add_argument("--out", default="schemas/xendit.postman_collection.json")
    args = parser.parse_args()

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)

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
