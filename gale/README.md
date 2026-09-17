# Gale — DriveTime / Carvana invoicing agent

Gale finds repair orders that are finished but unbilled, emails the invoice to
the customer, and posts the RO to Accounts Receivable in Tekmetric. It runs by
itself on the `gale-server` VM and reports to Reuben in Slack through Benny.

This directory is the source of truth for Gale's code. The VM is a deployment
of it, not the original.

## How a run works

1. `ro_tracker.py` reads the **RO Tracker** sheet and returns every DriveTime
   or Carvana RO marked `Complete` + `Balance Due`.
2. `daily_sweep.py` opens one Tekmetric browser session and, for each RO:
   downloads the invoice PDF, emails it (Recon jobs route to their own
   recipients), posts the RO to A/R, and records it in `state/gale_state.json`
   so it can never be invoiced twice.
3. Every RO is appended to the **Gale Invoicing Tracker** sheet — `Gale Log`
   for everything, `ROs A/R` for the ones that went out.
4. A summary email goes to Reuben, and the n8n watchdog posts the Slack digest.

## Schedule

| What | When | Where |
| --- | --- | --- |
| `daily_sweep.py` | 09:00, 12:40, 18:00 CDT | cron on gale-server |
| `session_keepalive.py` | every 2 hours | cron on gale-server |
| Watchdog + Slack digest | nightly | n8n (`Gale — Nightly Watchdog`) |

Invoicing happens **only** in the cron sweep. n8n observes and reports; it
never invoices. That separation is deliberate — see "Two systems" below.

## The four things that used to break it

1. **The login aged out.** `tekmetric.py` loaded the saved session but never
   wrote it back, so Tekmetric's rolling login expired every few days and
   every RO in a run failed with "the saved session has expired". Sessions are
   now saved on every open and close, and `session_keepalive.py` touches
   Tekmetric every two hours, so the login stays valid on its own.
2. **Gmail quota ended runs.** A 403 `rateLimitExceeded` mid-sweep killed the
   run — and the failure alert, being another Gmail send, died the same way,
   so it failed silently. All Gmail calls now retry with backoff.
3. **Two runs could overlap.** A long 6pm run was still going at 1am, racing
   the next scheduled run on the same ROs. A lock file now makes that
   impossible.
4. **Nobody could tell it had stopped.** Runs now write
   `state/last_run.json`, and the watchdog escalates on a session that has
   gone stale, a run that did not happen, or a backlog that keeps growing.

## When something is wrong

Gale alerts in Slack. The three messages that need you, and what to do:

- **"Tekmetric login needs a refresh"** — open Tekmetric in Chrome and log in
  as normal; the extension pushes the fresh session to the server. This should
  now be rare, because the keepalive holds the login open.
- **"No sweep has run in over N hours"** — the VM or cron is down. Check the
  VM is up, then `cd ~/gale && python3 daily_sweep.py 1` to run a single RO.
- **"N ROs waiting to invoice"** and climbing — Gale is running but something
  is failing per-RO. Look at the newest file in `~/gale/screenshots/` to see
  exactly what the page looked like when it gave up.

## Safe manual commands (on gale-server, in `~/gale`)

```bash
python3 gale_health.py            # read-only status, changes nothing
python3 session_keepalive.py      # prove the login works, refresh it
python3 daily_sweep.py 1          # invoice exactly ONE ro — real send
```

`daily_sweep.py` with no argument invoices everything that is ready. It sends
real invoices to real customers, so treat it as a production action.
