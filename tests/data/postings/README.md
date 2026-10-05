# Job-posting fixtures (R8 `job-posting-extraction`)

Six public job postings sampled from the VPS job store on 2026-10-05, one per source style:
Workday (NVIDIA, Cisco), Greenhouse (Coinbase, Anthropic), Indeed and LinkedIn. Emails and the one
person's name were removed.

- `<name>.txt`: the description as stored in `full_description`.
- `<name>.meta.json`: the row's metadata (title, site, company, location, salary column, strategy, url).
- `<name>.json`: hand-checked expected values for a few fields (role category, work mode, salary and
  some clear-cut ones). Fields not listed aren't checked.
