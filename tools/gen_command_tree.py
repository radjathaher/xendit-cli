#!/usr/bin/env python3
import argparse
import json
import re
import sys
import urllib.parse
from typing import Dict, List, Optional, Tuple

CAMEL_RE = re.compile(r"([a-z0-9])([A-Z])")


def camel_to_kebab(value: str) -> str:
    value = value.replace("/", "-").replace("_", "-").replace(" ", "-")
    value = CAMEL_RE.sub(r"\1-\2", value)
    value = re.sub(r"[^a-zA-Z0-9-]+", "-", value)
    value = re.sub(r"-+", "-", value)
    return value.strip("-").lower()


def normalize_op_name(value: str) -> str:
    name = camel_to_kebab(value)
    return name or "call"


def path_from_raw(raw: str) -> str:
    raw = raw.split("?")[0]
    if "://" in raw:
        parsed = urllib.parse.urlparse(raw)
        return parsed.path or "/"
    if raw.startswith("/"):
        return raw
    if raw.startswith("{{"):
        idx = raw.find("}}")
        if idx != -1:
            tail = raw[idx + 2 :]
            if not tail:
                return "/"
            if not tail.startswith("/"):
                tail = "/" + tail
            return tail
    return "/" + raw.lstrip("/")


def build_path(url: object) -> str:
    if isinstance(url, str):
        return path_from_raw(url)
    if isinstance(url, dict):
        path = url.get("path")
        if isinstance(path, list) and path:
            return "/" + "/".join(path)
        raw = url.get("raw") or ""
        if raw:
            return path_from_raw(raw)
    return "/"


def extract_path_params(path: str) -> List[str]:
    params = []
    for match in re.findall(r"\{([^}]+)\}|:([A-Za-z0-9_]+)|\{\{([^}]+)\}\}", path):
        name = next((m for m in match if m), None)
        if name:
            params.append(name)
    return params


def derive_resource(parents: List[str], path: str) -> str:
    if parents:
        base = parents[0]
        if base:
            return camel_to_kebab(base)
    segment = next((s for s in path.split("/") if s), "root")
    return camel_to_kebab(segment)


def collect_params(url: object, path: str) -> List[Dict]:
    params: Dict[Tuple[str, str], Dict] = {}

    for name in extract_path_params(path):
        params[("path", name)] = {
            "name": name,
            "flag": camel_to_kebab(name),
            "location": "path",
            "required": True,
        }

    if isinstance(url, dict):
        for var in url.get("variable") or []:
            key = var.get("key")
            if key:
                params[("path", key)] = {
                    "name": key,
                    "flag": camel_to_kebab(key),
                    "location": "path",
                    "required": True,
                }

        for query in url.get("query") or []:
            if query.get("disabled"):
                continue
            key = query.get("key")
            if key:
                params[("query", key)] = {
                    "name": key,
                    "flag": camel_to_kebab(key),
                    "location": "query",
                    "required": False,
                }

    return list(params.values())


def load_spec(path: str) -> Dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def is_openapi(spec: Dict) -> bool:
    return "openapi" in spec or "swagger" in spec or "paths" in spec


def is_postman(spec: Dict) -> bool:
    info = spec.get("info") or {}
    schema = info.get("schema") or ""
    return "item" in spec and "postman" in schema


def openapi_base_url(spec: Dict) -> str:
    servers = spec.get("servers") or []
    if servers and servers[0].get("url"):
        return servers[0]["url"]
    return "https://api.xendit.co"


def postman_base_url(spec: Dict) -> str:
    for var in spec.get("variable") or []:
        key = (var.get("key") or "").lower()
        if key in {"base_url", "baseurl", "api_url", "apiurl"}:
            value = var.get("value")
            if value:
                return str(value)
    return "https://api.xendit.co"


def build_from_openapi(spec: Dict) -> Dict:
    resources: Dict[str, Dict] = {}
    seen: Dict[str, set] = {}

    def add_op(resource: str, op: Dict) -> None:
        entry = resources.setdefault(resource, {"name": resource, "ops": []})
        entry["ops"].append(op)

    for path, methods in (spec.get("paths") or {}).items():
        if not isinstance(methods, dict):
            continue
        for method, details in methods.items():
            if method.lower() not in {"get", "post", "put", "patch", "delete", "head", "options"}:
                continue
            details = details or {}
            tags = details.get("tags") or []
            resource = camel_to_kebab(tags[0]) if tags else camel_to_kebab(path.split("/")[1] or "root")
            op_name = details.get("operationId") or f"{method}-{path}"
            op_name = normalize_op_name(op_name)

            used = seen.setdefault(resource, set())
            if op_name in used:
                op_name = normalize_op_name(f"{op_name}-{method}")
            idx = 2
            while op_name in used:
                op_name = normalize_op_name(f"{op_name}-{idx}")
                idx += 1
            used.add(op_name)

            params = []
            for param in details.get("parameters") or []:
                name = param.get("name")
                location = param.get("in")
                if not name or location not in {"path", "query"}:
                    continue
                params.append(
                    {
                        "name": name,
                        "flag": camel_to_kebab(name),
                        "location": location,
                        "required": bool(param.get("required")) or location == "path",
                    }
                )

            has_body = bool(details.get("requestBody"))
            add_op(
                resource,
                {
                    "name": op_name,
                    "method": method.upper(),
                    "path": path,
                    "description": details.get("summary") or details.get("description"),
                    "params": params,
                    "has_body": has_body,
                },
            )

    return {
        "version": 1,
        "base_url": openapi_base_url(spec),
        "resources": sorted(resources.values(), key=lambda r: r["name"]),
    }


def build_from_postman(spec: Dict) -> Dict:
    resources: Dict[str, Dict] = {}
    seen: Dict[str, set] = {}

    def add_op(resource: str, op: Dict) -> None:
        entry = resources.setdefault(resource, {"name": resource, "ops": []})
        entry["ops"].append(op)

    def dedupe(resource: str, name: str, method: str) -> str:
        used = seen.setdefault(resource, set())
        candidate = name
        if candidate in used:
            candidate = normalize_op_name(f"{name}-{method.lower()}")
        idx = 2
        while candidate in used:
            candidate = normalize_op_name(f"{name}-{idx}")
            idx += 1
        used.add(candidate)
        return candidate

    def walk(items: List[Dict], parents: List[str]) -> None:
        for item in items:
            if "item" in item:
                walk(item.get("item") or [], parents + [item.get("name") or ""])
                continue
            request = item.get("request") or {}
            method = (request.get("method") or "GET").upper()
            url = request.get("url") or ""
            path = build_path(url)
            resource = derive_resource(parents, path)
            name = normalize_op_name(item.get("name") or f"{method}-{path}")
            name = dedupe(resource, name, method)
            params = collect_params(url, path)
            body = request.get("body") or {}
            has_body = method in {"POST", "PUT", "PATCH"} and body.get("mode") not in (None, "none")
            add_op(
                resource,
                {
                    "name": name,
                    "method": method,
                    "path": path,
                    "description": item.get("description") or request.get("description"),
                    "params": params,
                    "has_body": has_body,
                },
            )

    walk(spec.get("item") or [], [])

    return {
        "version": 1,
        "base_url": postman_base_url(spec),
        "resources": sorted(resources.values(), key=lambda r: r["name"]),
    }


def merge_trees(trees: List[Dict]) -> Dict:
    base_urls = [tree.get("base_url") for tree in trees if tree.get("base_url")]
    unique_urls = [url for url in dict.fromkeys(base_urls) if url]
    if not unique_urls:
        base_url = "https://api.xendit.co"
    elif len(unique_urls) == 1:
        base_url = unique_urls[0]
    else:
        base_url = "https://api.xendit.co" if "https://api.xendit.co" in unique_urls else unique_urls[0]
        print(
            f"warning: multiple base URLs detected ({', '.join(unique_urls)}), using {base_url}",
            file=sys.stderr,
        )

    resources: Dict[str, Dict] = {}
    seen_keys: Dict[str, set] = {}
    seen_names: Dict[str, set] = {}

    for tree in trees:
        for res in tree.get("resources") or []:
            entry = resources.setdefault(res["name"], {"name": res["name"], "ops": []})
            used_keys = seen_keys.setdefault(res["name"], set())
            used_names = seen_names.setdefault(res["name"], {op["name"] for op in entry["ops"]})

            for op in res.get("ops") or []:
                key = (op["method"], op["path"])
                if key in used_keys:
                    continue
                name = op["name"]
                if name in used_names:
                    candidate = normalize_op_name(f"{name}-{op['method'].lower()}")
                    idx = 2
                    while candidate in used_names:
                        candidate = normalize_op_name(f"{name}-{idx}")
                        idx += 1
                    name = candidate
                merged = dict(op)
                merged["name"] = name
                entry["ops"].append(merged)
                used_keys.add(key)
                used_names.add(name)

    return {
        "version": 1,
        "base_url": base_url,
        "resources": sorted(resources.values(), key=lambda r: r["name"]),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate command tree from OpenAPI or Postman collection.")
    parser.add_argument("--spec", action="append", dest="specs")
    parser.add_argument("--out", default="schemas/command_tree.json")
    args = parser.parse_args()

    spec_paths = args.specs or [
        "schemas/xendit.postman_collection.json",
        "schemas/xendit.openapi.json",
    ]

    trees = []
    for spec_path in spec_paths:
        spec = load_spec(spec_path)
        if is_openapi(spec):
            trees.append(build_from_openapi(spec))
        elif is_postman(spec):
            trees.append(build_from_postman(spec))
        else:
            raise SystemExit(f"error: unsupported spec format in {spec_path}")

    tree = merge_trees(trees)

    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(tree, f, indent=2, sort_keys=True)
    print(args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
