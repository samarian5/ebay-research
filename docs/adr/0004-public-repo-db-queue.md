# 0004 — Public repo, queue in Postgres, no sealed artifacts

**Status:** accepted

## Context

The crawl must run in CI (ADR 0001). Two costs follow: Actions minutes, and the risk of
keywords leaking into public build logs.

## Decision

Host the crawler in a **public** repository, keep the work queue in **Postgres**
(`eb_keywords.due_at`), and never pass keywords through workflow inputs or sealed
artifacts. The workflow fires every 6 hours, asks the database what is due, and exits in
seconds when nothing is.

## Why

- ~375 minutes/month are needed; the private allowance is 2,000 but the public allowance is
  unmetered, so visibility is a hosting decision rather than a technical one.
- A database queue keeps keyword text out of `workflow_dispatch` payloads, which would
  otherwise sit in public run logs forever.
- No artifact sealing means no key management and no binary blobs in the repo; the crawl
  writes straight to Supabase.

## Consequences

- The Supabase service key lives in Actions secrets (encrypted with the repo public key),
  never in the repo.
- Anyone can read the workflow definition; nobody can read the keywords from it.
- The 6-hourly tick is a poll, not a schedule of work: cadence is per keyword
  (`interval_hours`), so adding a keyword never requires touching the workflow.
