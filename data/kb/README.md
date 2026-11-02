# Knowledge base (RAG source documents)

Every `.md` or `.txt` file in this folder (and subfolders) is loaded into the
Supabase `kb_documents` table by:

```bash
python run.py ingest
```

The agent can only state facts about City services that its `kb_search` tool
returns (policy rule C5), so this folder is where those facts come from.

Conventions:
- One topic per file, starting with a `# Title` line (used as the document title).
- Short paragraphs separated by blank lines; chunks are cut on blank lines.
- **Synthetic content only.** Everything here describes the fictional City of
  Gilead. No real residents, addresses or City systems.
- Nothing here overrides `config/policy.yaml`. Routing, required fields and
  escalation triggers come only from the policy store.

You can also add or edit documents directly in the Supabase dashboard
(`kb_documents` table) and then run `python run.py ingest --skip-files` to
re-chunk just the documents that changed. Set `active = false` to hide a
document from retrieval without deleting it. This README is not ingested.
