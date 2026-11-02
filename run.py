"""One entry point for every task (owner: E). Mirrors the Makefile.

    python run.py serve      start the chat site (http://HOST:PORT)
    python run.py ingest     re-chunk changed Supabase KB documents now (--import DIR to bulk upload)
    python run.py config     pull/push the config files in Supabase (see `python run.py config`)
    python run.py test       run the test suite
"""
from __future__ import annotations

import subprocess
import sys


def serve(args):
    import uvicorn

    from src.config import env
    uvicorn.run("src.web.server:app", host=env("HOST", "127.0.0.1"), port=int(env("PORT", "8311")),
                reload="--reload" in args, proxy_headers=True,
                forwarded_allow_ips=env("FORWARDED_ALLOW_IPS", "127.0.0.1"))


def ingest(args):
    from src.rag.ingest import main
    main(args)


def config(args):
    """config pull DIR: save every config file from Supabase into DIR.
    config push DIR: upload DIR's files to Supabase (seeds a new project).
    Refresh the test fixture with: python run.py config pull tests/fixtures/config"""
    from pathlib import Path

    from src.config import EDITABLE
    from src.tools.store import get_store
    if len(args) != 2 or args[0] not in ("pull", "push"):
        print(config.__doc__)
        sys.exit(1)
    from src.tools.store import MemoryStore
    store, folder = get_store(), Path(args[1])
    if isinstance(store, MemoryStore):   # no Supabase credentials: refuse rather than "succeed" in memory
        print("Supabase isn't configured (SUPABASE_URL and SUPABASE_SERVICE_KEY in .env, USE_MEMORY_STORE=0).")
        sys.exit(1)
    if args[0] == "pull":
        for row in store.list_config():
            target = folder / row["path"]
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(row["content"], encoding="utf-8")
            print(f"pulled {row['path']}")
        return
    for path in sorted(folder.rglob("*")):
        rel = path.relative_to(folder).as_posix()
        if path.is_file() and EDITABLE.match(rel):
            store.save_config(rel, path.read_text(encoding="utf-8"), "run.py config push", f"uploaded from {folder}")
            print(f"pushed {rel}")


def test(args):
    sys.exit(subprocess.call([sys.executable, "-m", "pytest", "-q", *args]))


TARGETS = {"serve": serve, "ingest": ingest, "config": config, "test": test}

if __name__ == "__main__":
    if len(sys.argv) < 2 or sys.argv[1] not in TARGETS:
        print(__doc__)
        sys.exit(1)
    TARGETS[sys.argv[1]](sys.argv[2:])
