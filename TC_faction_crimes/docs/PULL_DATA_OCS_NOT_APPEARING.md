# Why newly spawned OCs don’t appear after “Pull Data from TC API”

## Cause: pipeline and dashboard use different tables

1. **Pull Data** runs the `TC_faction_crimes` pipeline, which reads from the Torn API and writes to **one** BigQuery table. That table is set in **`TC_faction_crimes/config/TC_API_config.json`** under the crimes endpoint’s `"table"` field.

2. **The dashboard (OC Assignment, OCs Needed, etc.) always reads from**
   `torncity-402423.torn_data.v2_faction_40832_crimes-**raw**`.  
   That table is hardcoded in the dashboard and in `oc_email_generator.py`; it never reads from `-new`.

3. **There is no copy/sync from `-new` to `-raw`** anywhere in this repo. So if the pipeline writes to `-new`, the dashboard will never see that data.

Project docs (e.g. `INFORMATION_NEEDED.md`) describe the crimes table as **`v2_faction_40832_crimes-new`**. If your config uses that, then:

- Pull Data writes new OCs (and updates) into **`v2_faction_40832_crimes-new`**.
- The dashboard only queries **`v2_faction_40832_crimes-raw`**.
- So newly spawned OCs do not appear until something else copies data from `-new` to `-raw` (and nothing in this repo does that).

## Fix: make the pipeline write to the same table the dashboard reads

1. Open **`TC_faction_crimes/config/TC_API_config.json`** (create it from a template if needed).

2. Find the crimes endpoint (e.g. `v2_faction_40832_crimes` or the entry whose `"url"` is the faction crimes endpoint).

3. Set that endpoint’s **`"table"`** so the pipeline writes to **raw**, not new. Use either:
   - **Short form (recommended):**  
     `"table": "v2_faction_40832_crimes-raw"`  
     (the loader uses the configured GCP project and dataset, so the full table is `torncity-402423.torn_data.v2_faction_40832_crimes-raw`), or  
   - **Full table ID:**  
     `"table": "torncity-402423.torn_data.v2_faction_40832_crimes-raw"`

4. Save the config and run **Pull Data from TC API** again. Newly spawned OCs should then show up in the dashboard after the pull (and page reload).

## How to confirm

- After a pull, check the pipeline logs (e.g. Docker logs for `tc-faction-crimes-pipeline`). You should see a line like:  
  `Loading N records to ... v2_faction_40832_crimes-raw ...`  
  If it says `v2_faction_40832_crimes-new`, the dashboard will not see the new OCs until you change the config to `-raw` as above.

- In BigQuery, compare row counts or recent rows in:
  - `torncity-402423.torn_data.v2_faction_40832_crimes-new`
  - `torncity-402423.torn_data.v2_faction_40832_crimes-raw`  
  If only `-new` is updating after a pull, that confirms the mismatch; switching the config to `-raw` fixes it.
