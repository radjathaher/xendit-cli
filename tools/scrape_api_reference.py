#!/usr/bin/env python3
import argparse
import base64
import html as html_lib
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

DEFAULT_INDEX_URLS = [
    "https://docs.xendit.co/apidocs",
    "https://developers.xendit.co/api-reference",
]
DEFAULT_DOC_BASES = [
    "https://docs.xendit.co/apidocs",
    "https://developers.xendit.co/api-reference",
]
DEFAULT_API_BASE_URL = "https://api.xendit.co"

SERVER_STATE_RE = re.compile(
    r'<script id="serverApp-state" type="application/json">(.*?)</script>',
    re.DOTALL,
)
METHOD_URL_RE = re.compile(
    r'api-http-method">\s*([^<]+?)\s*</div>\s*<div class="api-url">\s*([^<]+?)\s*</div>',
    re.IGNORECASE,
)


def fetch_url(url: str) -> bytes:
    encoded = urllib.parse.quote(url, safe=":/?=&%")
    req = urllib.request.Request(encoded, headers={"User-Agent": "xendit-cli"})
    with urllib.request.urlopen(req) as resp:
        return resp.read()


def extract_server_state(html: str) -> dict:
    match = SERVER_STATE_RE.search(html)
    if not match:
        raise RuntimeError("unable to locate serverApp-state JSON")
    return json.loads(match.group(1))


def collect_api_articles(state: object, out: list[dict]) -> None:
    if isinstance(state, dict):
        if (
            state.get("articleType") == 1
            and state.get("slug")
            and state.get("operationType")
            and state.get("isPublic", True)
        ):
            out.append(state)
        for value in state.values():
            collect_api_articles(value, out)
    elif isinstance(state, list):
        for value in state:
            collect_api_articles(value, out)


def read_articles_from_index(url: str) -> list[dict]:
    html = fetch_url(url).decode("utf-8", errors="replace")
    state = extract_server_state(html)
    articles: list[dict] = []
    collect_api_articles(state, articles)
    return articles


def fetch_article_html(slug: str, bases: list[str]) -> tuple[str, str] | None:
    for base in bases:
        url = f"{base.rstrip('/')}/{slug}"
        try:
            html = fetch_url(url).decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                continue
            raise
        return html, url
    return None


def normalize_path(raw: str, api_host: str) -> str | None:
    cleaned = raw.strip()
    if not cleaned:
        return None
    if "://" in cleaned:
        parsed = urllib.parse.urlparse(cleaned)
        if parsed.netloc and parsed.netloc.lower() != api_host:
            return None
        path = parsed.path or "/"
    else:
        path = cleaned
    path = path.split("?", 1)[0]
    if not path.startswith("/"):
        path = "/" + path
    return path


def extract_method_paths(html: str, api_host: str) -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []
    for method_raw, url_raw in METHOD_URL_RE.findall(html):
        method = method_raw.strip().upper()
        url = html_lib.unescape(url_raw).strip()
        path = normalize_path(url, api_host)
        if not path:
            continue
        pairs.append((method, path))
    return pairs


def extract_path_params(path: str) -> list[str]:
    params: list[str] = []
    for match in re.findall(r"\{([^}]+)\}|:([A-Za-z0-9_]+)|\{\{([^}]+)\}\}", path):
        name = next((m for m in match if m), None)
        if name and name not in params:
            params.append(name)
    return params


def op_parameters(path: str) -> list[dict]:
    params = []
    for name in extract_path_params(path):
        params.append(
            {
                "name": name,
                "in": "path",
                "required": True,
                "schema": {"type": "string"},
            }
        )
    return params


def default_responses() -> dict:
    return {
        "200": {"description": "OK"},
        "400": {"description": "Bad Request"},
        "401": {"description": "Unauthorized"},
        "403": {"description": "Forbidden"},
        "404": {"description": "Not Found"},
    }


def default_request_body() -> dict:
    return {
        "required": False,
        "content": {"application/json": {"schema": {"type": "object"}}},
    }


def load_seed(path: str) -> dict | None:
    seed = Path(path)
    if not seed.exists():
        return None
    with seed.open("r", encoding="utf-8") as f:
        return json.load(f)


def merge_seed_paths(target: dict, seed: dict | None) -> None:
    if not seed:
        return
    for path, methods in (seed.get("paths") or {}).items():
        if path not in target["paths"]:
            target["paths"][path] = methods
            continue
        for method, detail in (methods or {}).items():
            if method not in target["paths"][path]:
                target["paths"][path][method] = detail


def build_openapi(
    articles: dict[str, dict],
    doc_bases: list[str],
    api_base_url: str,
    seed: dict | None,
) -> dict:
    api_host = urllib.parse.urlparse(api_base_url).netloc.lower()
    paths: dict[str, dict] = {}
    used_ids: set[str] = set()

    for slug, meta in sorted(articles.items()):
        fetched = fetch_article_html(slug, doc_bases)
        if not fetched:
            print(f"warning: failed to fetch article {slug}", file=sys.stderr)
            continue
        html, _url = fetched
        pairs = extract_method_paths(html, api_host)
        if not pairs:
            print(f"warning: no api-url found for {slug}", file=sys.stderr)
            continue
        summary = meta.get("title") or slug
        for method, path in pairs:
            method_key = method.lower()
            if method_key not in {"get", "post", "put", "patch", "delete", "head", "options"}:
                continue
            op_id = f"{method_key}-{slug}"
            if op_id in used_ids:
                idx = 2
                while f"{op_id}-{idx}" in used_ids:
                    idx += 1
                op_id = f"{op_id}-{idx}"
            used_ids.add(op_id)

            entry = paths.setdefault(path, {})
            if method_key in entry:
                continue
            detail = {
                "summary": summary,
                "operationId": op_id,
                "parameters": op_parameters(path),
                "responses": default_responses(),
            }
            if method_key in {"post", "put", "patch"}:
                detail["requestBody"] = default_request_body()
            entry[method_key] = detail
        time.sleep(0.05)

    spec = {
        "openapi": "3.0.3",
        "info": {
            "title": "Xendit API (scraped)",
            "version": resolve_version(),
        },
        "servers": [{"url": api_base_url}],
        "paths": paths,
    }
    merge_seed_paths(spec, seed)
    return spec


def resolve_version() -> str:
    cargo = Path(__file__).resolve().parent.parent / "Cargo.toml"
    if not cargo.exists():
        return "0.0.0"
    text = cargo.read_text(encoding="utf-8", errors="replace")
    match = re.search(r'^version\s*=\s*"([^"]+)"', text, re.MULTILINE)
    if match:
        return match.group(1)
    return "0.0.0"


def build_auth_header(api_key: str) -> str:
    token = base64.b64encode(f"{api_key}:".encode("utf-8")).decode("utf-8")
    return f"Basic {token}"


def validate_paths(spec: dict, api_base_url: str, api_key: str) -> None:
    if not api_key:
        print("warning: XENDIT_API_KEY missing; skipping validation", file=sys.stderr)
        return
    api_base = api_base_url.rstrip("/")
    headers = {
        "Authorization": build_auth_header(api_key),
        "User-Agent": "xendit-cli",
    }
    drop: list[tuple[str, str]] = []
    for path, methods in (spec.get("paths") or {}).items():
        method = methods.get("get")
        if not method:
            continue
        if extract_path_params(path):
            continue
        url = f"{api_base}{path}"
        req = urllib.request.Request(url, headers=headers, method="GET")
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                status = resp.status
        except urllib.error.HTTPError as exc:
            status = exc.code
        except Exception as exc:
            print(f"warning: {url} failed: {exc}", file=sys.stderr)
            continue
        if status == 404:
            drop.append((path, "get"))
        time.sleep(0.1)
    for path, method in drop:
        spec["paths"][path].pop(method, None)
        if not spec["paths"][path]:
            spec["paths"].pop(path, None)


def main() -> int:
    parser = argparse.ArgumentParser(description="Scrape Xendit API reference into OpenAPI.")
    parser.add_argument("--index-url", action="append", dest="index_urls")
    parser.add_argument("--doc-base", action="append", dest="doc_bases")
    parser.add_argument("--api-base-url", default=os.getenv("XENDIT_API_URL", DEFAULT_API_BASE_URL))
    parser.add_argument("--api-key", default=os.getenv("XENDIT_API_KEY"))
    parser.add_argument("--seed", default="schemas/xendit.openapi.json")
    parser.add_argument("--out", default="schemas/xendit.openapi.json")
    parser.add_argument("--validate", action="store_true")
    args = parser.parse_args()

    index_urls = args.index_urls or DEFAULT_INDEX_URLS
    doc_bases = args.doc_bases or DEFAULT_DOC_BASES

    articles: dict[str, dict] = {}
    for url in index_urls:
        try:
            for article in read_articles_from_index(url):
                slug = article.get("slug")
                if slug and slug not in articles:
                    articles[slug] = article
        except Exception as exc:
            print(f"warning: failed to parse index {url}: {exc}", file=sys.stderr)

    if not articles:
        print("error: no API articles discovered", file=sys.stderr)
        return 1

    seed = load_seed(args.seed)
    spec = build_openapi(articles, doc_bases, args.api_base_url, seed)
    if args.validate:
        validate_paths(spec, args.api_base_url, args.api_key)

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as f:
        json.dump(spec, f, indent=2, sort_keys=True)
    print(out_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
