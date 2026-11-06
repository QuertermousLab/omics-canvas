# GREAT — guide for agents and new sessions

User documentation: [README.md](README.md). The repository rules in [../AGENTS.md](../AGENTS.md) apply.

## Where it runs

- THOR: `great/server.py` on 127.0.0.1:8894 (`VIZHUB_GREAT_PORT`), systemd user unit `vizhub-great.service` from
  `../deploy/systemd/vizhub-great.service.in`; the gateway routes `/great/` there with the path unchanged.
  `../vizhubctl restart great` after any change to the Python files (web files are served from disk, no restart;
  bump their `?v=` in `web/index.html`). Restarting stops the analyses in progress: check the admin panel's *Running*.
- Data: `data/<assembly>/` (GREAT 4.0.4) and `data/v3/<assembly>/` (GREAT 3.0.0) (not in git;
  `tools/fetch_great_data.py`, 10-17 MB each, `--update` for new ontology releases, `--great 4|3` for one version). State: `../state/great/` (`jobs/<id>/`, `settings.json`; not in git) — the jobs are users' results, kept
  by the retention and storage limit; never delete them by hand except test jobs you made yourself.
- Sign-in (`../auth/`): with `OMICS_CANVAS_AUTH` set the page and `/great/api/` need a session and administrators come
  from it; `COMPAT` routes, `rgreat.R` and `/great/api/health` stay open and must never read server files
  (`server_files = False`).
- Workspaces and files are Genome Canvas's (`--canvas http://127.0.0.1:8000`): the page sends `X-Vizhub-Workspace`,
  `server.py` checks it against Genome Canvas's `/api/workspaces` and reads tracks through its `/data/<workspace>/…`
  URLs. Genome Canvas links here (GREAT button, track menu) when `/great/api/health` answers, and redirects its old
  `/genome-canvas/great/…` links.

## Rules

- Keep the rGREAT formats exact (JSON rows of 15 / 12 columns, the TSV's 3 header lines, `var _sessionName`, the
  ontology div grid): `tests/test_great.py` checks them, and a run of real rGREAT is the final test (R and rGREAT are
  installed on THOR): `source("http://171.65.68.140:8892/great/rgreat.R")`, then rGREAT's own example
  (`randomRegions(nr = 1000, genome = "hg19")`, seed 123) must give 885 associated regions and the vignette's distances.
- Standard library only, Python 3.8+ (CI runs the tests on 3.8). `web/base.css` copies Genome Canvas's tokens and
  components (Nord light / dark, fonts in `web/fonts/`): keep it in step with `../genome-canvas/styles.css`.
- Test with a server of your own: `python3 server.py --port 8790 --state <scratch>/state` (its own state; it still
  reads data/ and Genome Canvas's workspaces) — never submit test jobs to the live one without deleting them.

## Decisions already made

- **GREAT** reproduces GREAT 4.0.4's method and gene sets exactly but uses current ontology releases (2019's are not
  published). Its region position is `floor((start + end - 1) / 2)` and a minus-strand gene's TSS base is TSS − 1:
  with these, rGREAT's vignette regions give great.stanford.edu's associations and distances bp for bp (2026-11-05).
  **GREAT 3.0.0** (added 2026-11-06 at the user's request; 4.0.4 stays the default): a job's `version` picks its data
  (`great.data_key()`: `hg19` or `v3/hg19`); the page's version selector, a `version` field (`3`, `3.0`, `3.0.0`) or
  rGREAT's `/great/public-3.0.0/` path choose it, other versions (2.0.2) are refused. Its genes are GREAT's own
  `<assembly>.great3.0.genes.txt`; its ontologies follow GREAT 3.0.0's list, rebuilt from current sources (Placenta
  Disorders and BioCyc cannot be). Check it against rGREAT's 2018 vignette run (README): 18,041 genes, PNP's genome
  fraction 1.435477e-05, ATAD3C −2738 with `gr_is_zero_based = TRUE`.
  GREAT 3.0.0's other ontologies are also built for GREAT 4.0.4's genes (`extraOntologies` in the manifest; the user
  chose an opt-in, unchecked on the page): only `params.extra` jobs test them (`great.ontologies_of`), so default
  GREAT 4.0.4 jobs, rGREAT's ontology list and the batch TSV stay as GREAT 4.0.4's.
  Limits are live settings, defaults: 5 s per client, 3 analyses at once, 300 s queue wait, 30-day retention, a 20 GB
  storage limit (a cleanup thread applies both every 10 minutes; the admin panel deletes by hand), exports
  without GREAT's 500-term cap (the user asked for adjustable limits instead of GREAT's strict interval).
