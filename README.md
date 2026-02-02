# xendit-cli

Auto-generated Xendit CLI from Postman/OpenAPI specs. Designed for LLM discovery and direct scripting.

## Install

### Install script (macOS arm64 + Linux x86_64)

```bash
curl -fsSL https://raw.githubusercontent.com/radjathaher/xendit-cli/main/scripts/install.sh | bash
```

### Nix (binary fetch)

```bash
nix profile install github:radjathaher/xendit-cli
```

### Build from source

```bash
cargo build --release
./target/release/xendit --help
```

## Auth

Get an API key from your Xendit dashboard.

```bash
export XENDIT_API_KEY="xnd_..."
```

Optional override:

```bash
export XENDIT_API_URL="https://api.xendit.co"
```

## Discovery (LLM-friendly)

```bash
xendit list --json
xendit describe payment-requests create --json
xendit tree --json
```

Human help:

```bash
xendit --help
xendit payment-requests --help
```

## Examples

List operations in a resource:

```bash
xendit payment-requests --help
```

Call an endpoint (JSON body):

```bash
xendit payment-requests create \
  --body '{"amount": 10000, "currency": "IDR", "payment_method": {"type": "EWALLET"}}' \
  --pretty
```

## Update spec + command tree

```bash
tools/fetch_spec.py --out schemas/xendit.postman_collection.json
tools/gen_command_tree.py --spec schemas/xendit.postman_collection.json --spec schemas/xendit.openapi.json --out schemas/command_tree.json
cargo build
```

## Notes

- `tools/fetch_spec.py` scrapes the docs page for a signed Postman URL; override with `XENDIT_DOCS_URL` or `XENDIT_SPEC_URL` if needed.
- `schemas/xendit.openapi.json` is a minimal bootstrap spec (GET /balance).
- `--raw` includes status + headers.
- `--body` supports `@file.json` for large payloads.
