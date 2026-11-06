# ITU Space Services — Notice Tracking System

Documentation (English) · Chinese version: [TrackingNotif_Documentation_CN.md](TrackingNotif_Documentation_CN.md)

*Last updated: 16 October 2026*

---

## Contents

1. [Overview](#1-overview)
2. [Architecture](#2-architecture)
3. [Data Sources and Merge Rules](#3-data-sources-and-merge-rules)
4. [Data Refresh and Caching](#4-data-refresh-and-caching)
5. [First-Time Setup (per browser)](#5-first-time-setup-per-browser)
6. [User Guide](#6-user-guide)
7. [Notice Detail Popup and SRS Statistics](#7-notice-detail-popup-and-srs-statistics)
8. [API Reference](#8-api-reference)
9. [Repository Layout](#9-repository-layout)
10. [Operations Guide](#10-operations-guide)
11. [Troubleshooting](#11-troubleshooting)
12. [Known Issues and Future Work](#12-known-issues-and-future-work)
13. [Change Log](#13-change-log)

---

## 1. Overview

The **Notice Tracking System** is an internal web application for the ITU Space Services Department. It shows space-station notices in one searchable, filterable and exportable table, combining data from these internal databases:

| Source | Database | What it provides |
|--------|----------|------------------|
| **SNTrack** | MS Access `sntrdat.mdb` | BR tracking data (BRReg date, ADM, CIRC/pub, remarks, …) |
| **e-submission** | SQL Server `SpaceNetworkSystem` on `sydney.itu.int` | Submission data (SatName, DateOfReceive, TypeOfSubmission, Status, …) |
| **SNS (SRS tables)** | SQL Server `SpaceNetworkSystem`, schema `dbo` (live) | Res35 flag (`grp_res35`) and the coordination details in the row popup |
| **SRS** | MS Access `srs_all.mdb` (weekly copy) | ESIM resolution flags (Res156/169/123) |

All databases are opened **read-only**; the system never writes to them.

| Item | Value |
|------|-------|
| User URL | `https://intweb.itu.int/ITU-R/space/css/notification/index_api.html` |
| Backend server | `156.106.168.185`, HTTPS port `5001` |
| Frontend files (IIS share) | `\\intweb.itu.int\intwebroot\ITU-R\space\css\notification\` |
| Git repository | `https://github.com/Infinite-Ng/TrackingNotif.git` (branch `main`) |

---

## 2. Architecture

```
 Browser
   │  1. page + cached data          https://intweb.itu.int/.../notification/
   │◄───────────────────────────────  IIS (static files)
   │                                    ├── index_api.html
   │                                    └── data/cached_data.json  ◄──┐ written after
   │                                                                  │ every refresh
   │  2. /api/refresh, /api/data,     https://156.106.168.185:5001    │
   │     /api/details/<ntc_id>        Flask backend (backend/api.py) ─┘
   └─────────────────────────────────►  ├── in-memory cache + background refresher
                                        ├── SNTrack  (sntrdat.mdb, local copy)
                                        ├── e-submission + SNS SRS tables, Res35 (SQL Server)
                                        └── SRS      (srs_all.mdb, ESIM only)
```

**What happens when a user opens the page:**

1. The page loads `data/cached_data.json` from IIS and shows the table immediately.
2. It then triggers a refresh, exactly like clicking **Refresh** (see [Section 4](#4-data-refresh-and-caching)).
3. When the backend has re-queried the databases, the table is updated in place.

**Components:**

| Component | File | Role |
|-----------|------|------|
| Frontend | `frontend/index_api.html` | Single-page app (jQuery, DataTables, Bootstrap, flatpickr, SheetJS). Deployed by copying to the IIS share. |
| Backend | `backend/api.py` | Flask API: queries and merges the databases, keeps an in-memory cache, writes `cached_data.json`. |
| IIS config | `web.config` (on the share only) | Contains a reverse-proxy rule `api/*` → `http://156.106.168.185:5000`. **Currently not working** — see [Section 12](#12-known-issues-and-future-work). |

The backend can also serve the frontend itself at `https://156.106.168.185:5001/` (from `frontend/`). In that case the page uses its own origin for API calls; from any other origin it uses the hard-coded `BACKEND_URL` (`https://156.106.168.185:5001`).

---

## 3. Data Sources and Merge Rules

### 3.1 Sources and join keys

| Source | Location | Tables used | Join key |
|--------|----------|-------------|----------|
| SNTrack | `\\blue\dfs\br\BR_DATA\SPACE\SNTRACK\sntrdat.mdb` (+ `sntrapp.mdw` workgroup file) | `tblSpaceStnNotif` | `ntc_id` |
| e-submission | SQL Server `sydney.itu.int` / `SpaceNetworkSystem` | `res908.Res908`; Status from `dbo.Submissions` + `dbo.SubmissionsStatus` via `SubmissionId` | `SnsNtcId` = `ntc_id` |
| SNS (SRS tables, live) | SQL Server `sydney.itu.int` / `SpaceNetworkSystem`, schema `dbo` | `notice`, `grp`, `provn`, `tr_aff_ntw`, `coord_agree_ntw`, `all_aff_ntw`, `grp_aff_rec`, `grp_res35` | `ntc_id` |
| SRS (weekly copy) | `\\blue\dfs\br\BR_DATA\SPACE\SRS_DB\srs_all.mdb` | `notice`, `grp`, `e_srvcls` (ESIM classification); comparison tool `--source-srs` | `ntc_id` |

The SNTrack MDB is **copied to a local folder** (`LOCAL_DATA_DIR` in `api.py`) at the start of each refresh and queried from there, so a locked network file never blocks the backend. If the copy fails, the previous local copy is used.

### 3.2 Which field comes from where

- **SNTrack:** BRREG, 2d_date, ADM, PHASE, tgt_ntc_id, 11.32A, 11.41, subtoc, SUP, d_val_in, d_check_in, d_spr_out, d_complete, d_wmeeting, CIRC/pub, CIRC2/pub2, CIRC3/pub3, remarks, tex_remarks
- **e-submission:** DateOfReceive, SatName, long_nom, ntwk_org, act_code, TypeOfSubmission, Status, SNTrack ID (`sntrack_id`), SubmissionId, InternalReference, DocumentumReference
- **SRS (`srs_all.mdb`):** Res156 / Res169 / Res123 (rules in [ESIM_RES_Classification.md](ESIM_RES_Classification.md)).
- **SNS (live):** Res35 ([7.4](#74-res35-option-2)) and the SRS section of the row popup ([Section 7](#7-notice-detail-popup-and-srs-statistics)).

BRREG (SNTrack) and DateOfReceive (e-submission) mean the same thing but can differ, e.g. when a submission was returned.

### 3.3 Merge rules (`fetch_data()` in `api.py`)

The merge is a full outer join on `ntc_id`:

| Case | Rule |
|------|------|
| Notice in SNTrack (with or without e-submission match) | **Always kept.** If it has no e-submission TypeOfSubmission, it is labelled `NotificationOfSpaceStation`. |
| Notice only in e-submission, TypeOfSubmission = `NotificationOfEarthStation` | **Dropped** (the system tracks space stations only). |
| Notice only in e-submission, other types | Kept **only if** it has a `sntrack_id` **or** a non-empty Status. |
| Several e-submission rows with the same `SnsNtcId` (e.g. placeholder `1`) | All rows are kept. |

---

## 4. Data Refresh and Caching

### 4.1 When data is refreshed

| Trigger | What happens |
|---------|--------------|
| **User opens the page** | Cached data is shown first, then a refresh is requested automatically. |
| **User clicks Refresh** | A refresh is requested. |
| **No user activity** | The backend refreshes on its own **once a day** (`REFRESH_INTERVAL_SECONDS`, default `86400`). |
| **Backend start-up** | If the cache file is older than 5 minutes (or missing), a refresh runs immediately. |

To protect the databases, the page **skips** the refresh when the data on screen is less than 1 minute old. On the backend, requests that arrive while a refresh is running are merged: at most one more refresh is queued.

### 4.2 Refresh sequence

1. The page sends `POST /api/refresh`. The backend answers immediately (`202`) and wakes the background refresher thread.
2. The refresher syncs the SNTrack MDB, queries all three databases, merges the data and stores it in memory. A full refresh takes about **20 seconds**.
3. Meanwhile the page polls `GET /api/data` every 3 seconds (max. 20 attempts). As soon as the returned `timestamp` is less than 1 minute old, the table is updated and shows **"Synced: dd.mm.yyyy HH:MM:SS"**.
4. If `POST /api/refresh` fails (backend down, certificate not trusted), the page **stops immediately**, keeps the cached data and shows *"Server not reachable – showing cached data"* in the status indicator (visible in Dev Mode).

`/api/data` never queries the databases directly. It always returns the in-memory cache, so it answers instantly even when a database is slow.

### 4.3 Cache files

After every successful refresh the backend writes the same JSON to two places:

| File | Used by |
|------|---------|
| `TrackingNotif/frontend/data/cached_data.json` | Backend start-up (loaded into memory), and the Flask-served page |
| `\\intweb.itu.int\intwebroot\ITU-R\space\css\notification\data\cached_data.json` | The IIS-served page (first thing it loads) |

```json
{ "generated_at": "2026-10-16T17:17:08.792043+00:00", "data": [ ... ] }
```

Files are written **atomically**: first to `cached_data.json.tmp`, then swapped in with `os.replace()`. A browser reading the file during a refresh therefore always gets a complete file. (Before October 2026 the file was overwritten in place, which could make the page show no data.)

If a refresh fails, the previous data stays in memory and in the files; `/api/data` then returns `"stale": true`.

---

## 5. First-Time Setup (per browser)

The backend uses a **self-signed certificate**. Until a browser trusts it, the page still shows the cached data, but **Refresh** and the SRS part of the row popup cannot reach the backend.

1. Open `https://156.106.168.185:5001/api/health`.
2. Click **Advanced → Proceed to 156.106.168.185 (unsafe)** (wording depends on the browser).
3. You should see `{"status": "healthy", "timestamp": "..."}`.
4. Go back to the tracking page and reload it.

This is needed once per browser, and again whenever the certificate is regenerated (last regenerated: **16 October 2026**). With **Dev Mode** on, the page shows a yellow banner with this link when the certificate is not trusted.

---

## 6. User Guide

### 6.1 Opening the application

Open `https://intweb.itu.int/ITU-R/space/css/notification/index_api.html` in Chrome or Edge.

Do **not** open the HTML file directly from Windows Explorer (`file://`): the browser blocks loading `cached_data.json` that way and the table stays empty.

The line next to "Data Preview" shows how current the data is:

| Text | Meaning |
|------|---------|
| `Generated: <date time> (N records)` | Data from the cache file, generated at that time |
| `Synced: <date time> (N records)` | Fresh data just received from the backend |

### 6.2 Filters

**Top filter bar:**

| Control | Description |
|---------|-------------|
| **Type of Submission** (chips) | Multi-select. Default: *NotifSS* and *ResubSS*. Click the box to open the list; click a chip's **×** to remove it; the right-hand **×** clears all. |
| **Status** (chips) | Multi-select, same behaviour. Default: all statuses. |
| **BRReg date range** | From / To date pickers (dd.mm.yyyy). Both ends are inclusive. |

**Column filters** (second header row): text columns support substring search; date columns have From/To pickers; ADM, TypeOfSubmission, Status and SUP have multi-select lists; flag columns (11.32A, subtoc, Res156, …) have drop-downs. The top-bar and in-table TypeOfSubmission/Status filters stay in sync.

### 6.3 Search

The **Search** box searches all visible columns. Dates can be searched in either form, e.g. `23.02.2025` or `2025-02-23`.

### 6.4 Refresh

Click **Refresh** to load the latest data (about 20 seconds). The button shows *Refreshing…* until the new data arrives. If the data is less than 1 minute old, nothing is done.

### 6.5 Reset

**Reset** restores all filters: TypeOfSubmission → NotifSS + ResubSS; everything else → cleared / "All".

### 6.6 Export

**Export** downloads the currently **filtered rows and visible columns** as `notice_report_YYYYMMDD.xlsx`. Date columns are real Excel dates formatted `dd.mm.yyyy`, so they still sort and filter as dates in Excel.

### 6.7 Column visibility

Use the column chips at the top to show or hide columns, then click **Apply**. Settings are stored in the browser (`localStorage`). **Reset** (next to Apply) restores the default columns.

### 6.8 Dates

All dates are shown as **dd.mm.yyyy** (e.g. `07.10.2026`), in the table, the popup, the date pickers and the export. Internally, sorting and filtering use ISO dates (`yyyy-mm-dd`).

### 6.9 Row details

Click any row to open the notice detail popup ([Section 7](#7-notice-detail-popup-and-srs-statistics)).

### 6.10 Dev Mode

The **Dev Mode** switch (top right) shows the API status indicator and, when needed, the certificate banner. The setting is remembered per browser.

### 6.11 TypeOfSubmission short names

| Short name | Full name |
|------------|-----------|
| NotifSS | NotificationOfSpaceStation |
| ResubSS | Resubmission |
| API | APINotSubjectToCoordination |
| CR | CoordinationRequest |
| API-Info | AdvancePublicationInformation |
| DDI | DueDiligenceInformation |
| Res49 | NotificationUnderResolution49 |
| Sup | Suppression |
| Mod | Modification |

---

## 7. Notice Detail Popup and SRS Statistics

Clicking a row opens a popup with three sections, grouped by data source:

| Section | Content | Source |
|---------|---------|--------|
| SNTrack | All SNTrack fields of the row | Already in the table data |
| e-submission | All e-submission fields of the row | Already in the table data |
| Res35 | Whether the notice is Res35 (Option 2), and its groups in `dbo.grp_res35` with milestones ([7.4](#74-res35-option-2)) | Queried live: `GET /api/details/<ntc_id>` |
| SRS | Res156/169/123, `prov`, `ntf_occurs`, `prov_desc`, coordination statistics from `provn` and `tr_aff_ntw`, row counts of `coord_agree_ntw` and `all_aff_ntw` | Res156/169/123: already in the table data; the rest queried live: `GET /api/details/<ntc_id>` |

"Queried live" means the tables in schema `dbo` of SQL Server `sydney.itu.int` / `SpaceNetworkSystem` (SELECT only), no longer `srs_all.mdb`. The `dbo` tables `notice`, `grp`, `provn`, `tr_aff_ntw`, `coord_agree_ntw`, `all_aff_ntw` and `grp_aff_rec` have the same names and fields as in `srs_all.mdb` (plus `ValidFrom` / `ValidTo`); `srs_all.mdb` is a weekly copy, `dbo` is live. A spot check of 160 notices gave identical results for the 143 that are in `srs_all.mdb`; the other 17 exist only in SNS (new submissions, and some old notices never published in an IFIC). SNS also has the schemas `ific` (published versions per IFIC) and `history_dbo` (history); the popup does not use them.

Empty values are shown as blank fields. A notice that is not in the SNS database shows *"Notice not found in SNS database."*

### 7.1 How SRS tables link to a notice (same in SNS `dbo` and `srs_all.mdb`)

| Table | Link |
|-------|------|
| `notice` | `ntc_id` |
| `provn` | `grp_id` → `grp.ntc_id` (provn has no `ntc_id` column) |
| `tr_aff_ntw` | `ntc_id` |
| `coord_agree_ntw` | `ntc_id` |
| `all_aff_ntw` | `aff_rec_id` → `grp_aff_rec.grp_id` → `grp.ntc_id` (**rule pending confirmation**) |

### 7.2 Statistics rules

Based on Wu's e-mail "The coord_st statistics result" (24 February 2026). `provn` and `tr_aff_ntw` use the same rules. Administrations are **de-duplicated** by `adm` (an adm on many rows counts once); rows with an empty `adm` are not counted.

| Popup field | Meaning | Reference Excel column |
|-------------|---------|------------------------|
| agree_st | Per symbol: number and list of distinct adm | symbol O = J `agree_st_o_count` / L `agree_st_o_adm_list` |
| coord_st | Per symbol: number and list of distinct adm | symbol O = K `coord_st_o_count` / O `coord_st_o_adm_list` |
| coord_st details | adm lists grouped by coord_st symbol, then by agree_st value | symbol O = N `coord_st_o_details` |
| coord_prov | Distinct coord_prov values | provn = M `coord_prov_values`; tr_aff_ntw = Q `tr_aff_ntw_coord_prov` |
| has data (tr_aff_ntw only) | Whether the notice has rows in the table | P `info in tr_aff_ntw_data` |
| coord_agree_ntw | Row count (table has no primary key; no de-duplication) | — |
| all_aff_ntw | Count of distinct `aff_rec_id` | — |

`tr_aff_ntw` is cleared after the coordination wizard has run, so most notices have no data there; this is normal.

### 7.3 Checking against the reference Excel

`tools/compare_coord_stats.py` compares columns J–Q of the reference workbook notice by notice (read-only):

```bat
tracking_env_32\python.exe tools\compare_coord_stats.py coord_st_statistics_part1.xlsx result.csv
tracking_env_32\python.exe tools\compare_coord_stats.py coord_st_statistics_part1.xlsx result.csv --source-srs
tracking_env_32\python.exe tools\compare_coord_stats.py coord_st_statistics_part1.xlsx result.csv --source-mdb
```

- Without options: queries the SNS database `dbo` (the data the popup uses), with the account from `backend/.flaskenv`.
- `--source-srs`: queries `srs_all.mdb` (weekly copy) instead.
- `--source-mdb`: reads each administration's submitted MDB (Excel column `mdb_file`) instead — the workbook's own source — to check the counting logic independently.
- Differences between SNS / `srs_all.mdb` and the submitted MDB are expected: BR processing changes the data (e.g. an adm with coord_st = 'O' at submission becomes agree_st = 'O' in SRS; coord_prov prefix `X/` becomes `11.41/`; tr_aff_ntw is cleared).

### 7.4 Res35 (Option 2)

Resolution 35 is the milestone-based approach for non-GSO systems. Hierarchy: Network (notice) → Beam (group) → E/R → SA_number → Service Area. **If one group of a notice is under Res35, the whole notice is Res35.**

Option 2 uses the result BR has already computed: `dbo.grp_res35` in the SNS database lists every group under Res35 (`grp_res35.grp_id` → `grp.ntc_id`), with milestone data: `ms_step` (M0–M3), `f_ms_met` (met or not) and `d_ms_next_deadline` (next deadline). On 22 Oct 2026 the table had 48,960 groups in 140 notices, all non-GSO.

- Main table **Res35** column: Yes when the notice has any group in `dbo.grp_res35`; updated on every refresh.
- Popup **Res35** section: Yes (with the number of groups) / No, and the group list (grp_id, beam, E/R, frequency, milestone, met, next deadline; at most 500 rows shown). Notices that are not non-GSO are flagged, since Res 35 applies to non-GSO systems only.

Option 1 (computing Res35 from the resolution text and cross-checking it against Option 2) is left for a later version.

---

## 8. API Reference

Base URL: `https://156.106.168.185:5001`

| Method | Path | Rate-limited | Description |
|--------|------|:---:|-------------|
| GET | `/api/data` | ✓ | Returns the in-memory cache: `{success, count, timestamp, stale, data}`. `503` until the first data has been loaded. |
| POST / GET | `/api/refresh` | ✓ | Schedules a background refresh; returns `202` immediately with `current_timestamp`. |
| GET | `/api/details/<ntc_id>` | ✓ | Coordination statistics and Res35 for one notice (popup), queried live from SNS: `{success, details, res35, errors}`. `400` if `ntc_id` is not numeric, `503` if the SNS database is unreachable, `500` if both parts fail (if only one fails, the other is returned and the error is listed in `errors`). |
| GET | `/api/health` | — | `{"status": "healthy", "timestamp": ...}` |
| GET | `/`, `/index_api.html`, `/<file>` | — | Serves files from `frontend/`. |

**Rate limit:** 30 requests per 60 seconds per client IP (in memory), shared by all rate-limited endpoints. Exceeding it returns `429`. One page-open refresh uses about 1 + 7 requests.

**CORS:** allowed origins are the backend itself (`http(s)://156.106.168.185:5001`, `localhost:5001`, `127.0.0.1:5001`), `http(s)://intweb.itu.int` and `null` (`file://`). Add more with `FRONTEND_ORIGIN` (comma-separated).

---

## 9. Repository Layout

```
TrackingNotif/
├── backend/
│   ├── api.py                 Flask backend (all server logic)
│   ├── start_api.bat          Start script (admin, firewall rule, M: drive, python api.py)
│   ├── generate_cert.py       Creates cert.pem / key.pem (self-signed, 10 years)
│   ├── requirements.txt       Python dependencies
│   ├── .flaskenv.example      Template for .flaskenv (settings and credentials)
│   └── .flaskenv, *.pem, *.log   Local only, git-ignored
├── frontend/
│   ├── index_api.html         Main page (deployed to IIS as index_api.html)
│   ├── index_zhiruo.html      Development copy of the main page (same content)
│   ├── index.html, index_tracking.html   Legacy pages reading static data/data.json
│   └── data/                  cached_data.json (git-ignored), data.json (legacy)
├── tools/compare_coord_stats.py   SRS statistics check against the reference Excel
├── dataTransfer/, auto_update_data.bat   Legacy static-JSON export (not used by index_api.html)
└── docs/                      This documentation, ESIM_RES_Classification.md
```

On the IIS share, `index_api_.html` is a backup of the page **before** the October 2026 changes (US date format, no popup).

---

## 10. Operations Guide

### 10.1 Configuration (`backend/.flaskenv`)

Copy `backend/.flaskenv.example` to `backend/.flaskenv` and fill in the values. `.flaskenv` is git-ignored and must never be committed. **Passwords have no default** — if one is missing, the backend starts but logs a warning and the related database connection fails.

| Variable | Required | Default | Description |
|----------|:---:|---------|-------------|
| `SQL_SERVER_PASSWORD` | ✓ | — | SQL Server password |
| `MDB_PASSWORD` | ✓ | — | SNTrack MDB password |
| `SQL_SERVER_HOST` | | `sydney.itu.int` | SQL Server host |
| `SQL_SERVER_DB` | | `SpaceNetworkSystem` | SQL Server database |
| `SQL_SERVER_USER` | | `css_sns_ro_notif` (read-only) | SQL Server user |
| `MDB_USER` | | `spruser01` | SNTrack MDB user |
| `SRS_MDB_PATH` | | `\\blue\dfs\br\BR_DATA\SPACE\SRS_DB\srs_all.mdb` | SRS database |
| `REFRESH_INTERVAL_SECONDS` | | `86400` (1 day) | Automatic refresh interval |
| `FRONTEND_ORIGIN` | | — | Extra CORS origins, comma-separated |

Paths that are still hard-coded in `api.py`: `LOCAL_DATA_DIR` (local MDB copy), the network MDB/MDW paths, and the IIS cache path in `_write_cache_files()`.

### 10.2 Python environment and dependencies

- Use a Python environment that has `backend/requirements.txt` installed (`flask`, `flask-cors`, `pyodbc`, `python-dotenv`, `waitress`). On the current server this is the conda env `C:\Users\xianwu\Anaconda3\envs\codingSpace`.
- `generate_cert.py` also needs `cryptography`, which is **not** in `requirements.txt` and not in that env; the base Anaconda Python (`C:\Users\xianwu\Anaconda3\python.exe`) has it.
- The **Microsoft Access Database Engine** (ODBC driver) must be installed, with the same bitness (32/64-bit) as Python.

### 10.3 Starting the backend

The backend **must run over HTTPS on port 5001**, because the page calls `https://156.106.168.185:5001`.

```bat
cd C:\Users\xianwu\Desktop\codingSpace\TrackingNotif\backend
C:\Users\xianwu\Anaconda3\envs\codingSpace\python.exe api.py
```

`python api.py` uses `cert.pem` / `key.pem` when both exist and are valid. `start_api.bat` does the same and also elevates to administrator, adds the firewall rule for port 5001, kills any process already on port 5001 and maps `M:`; note that it calls plain `python`, so make sure that resolves to the right environment.

> ⚠️ **Do not** start it with `waitress --listen=0.0.0.0:5001`. Waitress serves plain HTTP only; the page's HTTPS requests then hang and never reach the backend. Waitress is only suitable behind an HTTPS reverse proxy (see [Section 12](#12-known-issues-and-future-work)).

To run it in the background with a log file (as on the server since 16 Oct 2026):

```powershell
$b = 'C:\Users\xianwu\Desktop\codingSpace\TrackingNotif\backend'
Start-Process -FilePath 'C:\Users\xianwu\Anaconda3\envs\codingSpace\python.exe' -ArgumentList 'api.py' `
  -WorkingDirectory $b -WindowStyle Hidden `
  -RedirectStandardOutput "$b\api_server.out.log" -RedirectStandardError "$b\api_server.log"
```

A background process stops when the machine restarts or the user logs off; start it again after a reboot.

### 10.4 Checking and stopping the backend

```powershell
# Is it up, and is it HTTPS?  (-k = accept the self-signed certificate)
curl.exe -k https://156.106.168.185:5001/api/health

# Which process holds port 5001, and how was it started?
netstat -ano | findstr :5001
Get-CimInstance Win32_Process -Filter "ProcessId=<PID>" | Select-Object CommandLine

# Stop it
taskkill /PID <PID> /F
```

The log (`backend\api_server.log`) shows every request and each refresh, e.g. `POST /api/refresh … 202` and `Background refresh complete: 21023 records`.

### 10.5 SSL certificate

Files: `backend/cert.pem` and `backend/key.pem` (git-ignored). The certificate covers `156.106.168.185`, `localhost` and `127.0.0.1`, valid for 10 years. `key.pem` is unencrypted — restrict its file permissions.

```bat
cd C:\Users\xianwu\Desktop\codingSpace\TrackingNotif\backend
C:\Users\xianwu\Anaconda3\python.exe generate_cert.py
```

Then restart the backend. **After regenerating, every user must trust the certificate again** ([Section 5](#5-first-time-setup-per-browser)).

### 10.6 Deploying frontend changes

1. Edit and test `frontend/index_api.html` in the repository.
2. Back up the live file on the share, then copy the new one over it:
   `\\intweb.itu.int\intwebroot\ITU-R\space\css\notification\index_api.html`
3. Ask users to reload the page (Ctrl+F5 if the browser cached the old version).
4. Commit the same file to Git so the repository matches what is deployed.

Backend changes (`api.py`) only take effect after restarting the backend.

### 10.7 Changing the server IP

1. Regenerate the certificate for the new IP (edit the IP in `generate_cert.py` first).
2. In `frontend/index_api.html`, update `const BACKEND_URL = 'https://<NEW_IP>:5001';` and the link in the certificate banner.
3. Update the origins in `_ALLOWED_ORIGINS` in `api.py`, and the target in `web.config` if the proxy is used.
4. Restart the backend, redeploy the page, and have users trust the new certificate.

---

## 11. Troubleshooting

| Symptom | Likely cause | Fix |
|---------|-------------|-----|
| Table is empty | Page opened from Explorer (`file://`) | Use the `https://intweb.itu.int/...` URL |
| Table is empty (status "No cached data available") | `cached_data.json` missing or unreadable | Check the file on the share; run a refresh. (Half-written files can no longer occur since atomic writes.) |
| Refresh does nothing; backend log shows no requests | Backend not running, or running as **plain HTTP** | `curl.exe -k https://156.106.168.185:5001/api/health`; if it fails, check the start command ([10.3](#103-starting-the-backend)) |
| "Server not reachable – showing cached data" | Backend down, or this browser does not trust the certificate | Check the backend; follow [Section 5](#5-first-time-setup-per-browser) |
| Backend crashes at start with `ssl.SSLError: [SSL] PEM lib` | `cert.pem` / `key.pem` empty or invalid | Regenerate the certificate ([10.5](#105-ssl-certificate)) |
| Popup shows "Could not load SRS data" or "SNS database not reachable" | Backend unreachable, or SQL Server unreachable / account invalid | Check the backend and the SQL Server account in `.flaskenv` |
| Refresh shows "Refresh may be incomplete" / "Refresh timed out" | Refresh took longer than the polling window (~70 s) | Check the backend log for slow or failing database connections |
| `429 Too Many Requests` | Rate limit (30 requests/min per IP) | Wait a minute |
| Data does not change after refresh | A database connection failed; previous data kept (`stale: true`) | Check credentials in `.flaskenv` and the log |
| Port 5001 already in use | Old process still running | Stop it ([10.4](#104-checking-and-stopping-the-backend)) |
| Other machines show cached data only, backend is fine | CORS origin not allowed | Add the origin via `FRONTEND_ORIGIN` |

---

## 12. Known Issues and Future Work

- **Self-signed certificate friction.** Every user must trust the certificate once per browser. Better: route the API through IIS so the page calls its own site. `web.config` already has a rule `api/*` → `http://156.106.168.185:5000`, but it currently fails with **IIS error 500.50** (URL Rewrite, access denied) — most likely because the rule sets `HTTP_X_FORWARDED_HOST`, which is not in the server's allowed server variables. Fixing it needs an IIS administrator (or removing that `<serverVariables>` block), then running the backend with waitress over HTTP on port 5000 and pointing `BACKEND_URL` at the relative `api/` path.
- **Backend is not a Windows service.** It stops on reboot/logoff. Consider a scheduled task at start-up or a service wrapper.
- **Res35 failure is silent:** if the `grp_res35` query fails during a refresh, every row shows Res35 = No instead of "unknown". In the popup, a part that failed is left blank; the `errors` list from `/api/details` is not shown.
- **Legacy script credentials:** `dataTransfer/data2Json.py` still has the old SQL Server account and passwords as hard-coded fallbacks (also in git history). Remove them, or retire the script.
- **Rule pending confirmation:** the `all_aff_ntw` link in the popup ([7.1](#71-how-srs-tables-link-to-a-notice-same-in-sns-dbo-and-srs_allmdb)).
- **Dead code:** `loadDataFromAPI()` in `index_api.html` is never called (it also references an undefined `filteredData`).
- **Duplicate pages:** `index_api.html` and `index_zhiruo.html` are identical; keep them in sync or remove the copy.
- **Hard-coded paths** in `api.py` (local data folder, IIS cache path) make moving the server harder.
- `cryptography` is missing from `requirements.txt` (needed by `generate_cert.py`).

---

## 13. Change Log

### October 2026

- **SQL Server account changed** to the read-only account `css_sns_ro_notif` (set in `backend/.flaskenv`).
- **Popup coordination statistics read live from SNS** (xuzhiruo, PR #2): `notice`, `provn`, `tr_aff_ntw`, `coord_agree_ntw`, `all_aff_ntw` now come from SQL Server `SpaceNetworkSystem.dbo` instead of the weekly `srs_all.mdb`; the comparison tool queries SNS by default and has a new `--source-srs` option.
- **Res35 (Option 2)** (xuzhiruo, PR #2): based on `dbo.grp_res35`; new Res35 column in the main table; the popup shows the groups and milestones.
- **Notice detail popup** (by xuzhiruo, PR #1): click a row to see all fields grouped by source, plus live SRS data via the new `/api/details/<ntc_id>` endpoint. New tool `tools/compare_coord_stats.py`.
- **Dates as dd.mm.yyyy** everywhere (table, popup, pickers, timestamps); Excel export writes real dates.
- **Refresh policy:** data is refreshed when a user opens the page or clicks Refresh; otherwise once a day (was every 10 minutes).
- **Atomic cache writes:** `cached_data.json` is written to a temp file and swapped in, fixing an empty table when the page loaded during a refresh.
- **Faster failure:** if the backend cannot be reached, the page stops at once and keeps the cached data, instead of polling for ~4 minutes.
- **HTTPS restored:** `cert.pem` / `key.pem` had been empty since June 2026 and the backend had been started as plain HTTP, so refresh requests never reached it. The certificate was regenerated on 16 Oct 2026 and the backend restarted with `python api.py`.

### Earlier (2026)

- Show SNTrack-only space-station notices; exclude earth-station submissions.
- Keep all e-submission rows that share a placeholder `SnsNtcId`.
- In-memory cache with background refresher; disk cache loaded at start-up.
- ESIM resolution classification (Res156/169/123) from SRS.
- Credentials moved to environment variables; CORS restrictions; rate limiting.
