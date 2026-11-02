"""One entry point for every task (owner: E). Mirrors the Makefile.

    python run.py serve      start the chat site (http://HOST:PORT)
    python run.py ingest     load data/kb into the Supabase knowledge base
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


def test(args):
    sys.exit(subprocess.call([sys.executable, "-m", "pytest", "-q", *args]))


TARGETS = {"serve": serve, "ingest": ingest, "test": test}

if __name__ == "__main__":
    if len(sys.argv) < 2 or sys.argv[1] not in TARGETS:
        print(__doc__)
        sys.exit(1)
    TARGETS[sys.argv[1]](sys.argv[2:])
