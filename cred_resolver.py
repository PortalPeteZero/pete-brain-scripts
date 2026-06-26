"""Shared credential/secret resolver for pete-brain-scripts helpers.

ONE place to resolve secret paths under VAULT (which the boot kernel sets to /tmp/pbs and
materialises secrets into). Adopt incrementally in any helper:

    from cred_resolver import secret_path, load_json, load_text, have
    keys = load_json("command-centre-supabase-keys.json")
    token = load_text("supabase-token")

Why: helpers used to each hardcode their own secret/path logic, so a path that was right in the
old local vault broke under /tmp/pbs (the "vision-api class" bug, fixed 2026-06). Centralising it
means a path is correct everywhere or nowhere — and `cc-boot-smoketest.py` can check it.
"""
import os, json

VAULT = os.environ.get("VAULT", "/tmp/pbs")
SEC = os.path.join(VAULT, "Library", "processes", "secrets")


def secret_path(name: str) -> str:
    return os.path.join(SEC, name)


def have(name: str) -> bool:
    return os.path.isfile(secret_path(name))


def load_text(name: str) -> str:
    with open(secret_path(name), encoding="utf-8") as f:
        return f.read().strip()


def load_json(name: str):
    with open(secret_path(name), encoding="utf-8") as f:
        return json.load(f)
