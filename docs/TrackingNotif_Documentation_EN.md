# ITU Space Services — Notice Tracking System

## Documentation (English)

---

## 1. Product Overview

The **Notice Tracking System** is an internal web application for the ITU Space Services Department. It provides a searchable, filterable, and exportable view of space-network notice submissions retrieved from two internal databases:

| Source | Type | Connection |
|--------|------|-----------|
| `sntrdat.mdb` | MS Access (read-only) | `M:\BR_DATA\SPACE\SNTRACK\sntrdat.mdb` |
| `SpaceNetworkSystem` | SQL Server | `sydney.itu.int` |

The frontend is a single-page HTML/JavaScript application served by a Flask backend over HTTPS.

---

## 2. System Architecture

```
Browser (user)
    │
    │  HTTPS (port 5001)
    ▼
Flask backend (api.py)
    │  156.106.168.185:5001
    │
    ├── /api/data        → returns notice records as JSON
    ├── /api/health      → health check endpoint
    ├── /api/refresh     → forces a fresh data fetch
    └── /                → serves index_api.html (frontend)
         └── /data/cached_data.json  → offline backup cache
```

**Two access methods for the frontend:**

| Method | URL | Notes |
|--------|-----|-------|
| Via Flask (recommended) | `https://156.106.168.185:5001/` | Served directly by backend |
| Via IIS file share | `\\intweb.itu.int\intwebroot\ITU-R\space\css\notification\index_api.html` | Static HTML file; JavaScript automatically points to `https://156.106.168.185:5001` for API calls |

---

## 3. First-Time Setup (per browser)

Because the backend uses a **self-signed SSL certificate**, browsers will block the API connection until the certificate is trusted.

**Steps:**
1. Open `https://156.106.168.185:5001/api/health` in your browser.
2. Click **Advanced → Proceed to 156.106.168.185 (unsafe)** (or equivalent for your browser).
3. You will see a plain JSON response `{"status": "ok"}` — this confirms the certificate is trusted.
4. Return to the tracking page and reload it.

You only need to do this once per browser. The certificate is valid for 10 years.

---

## 4. User Guide

### 4.1 Opening the Application

- **Preferred:** Navigate to `https://156.106.168.185:5001/` in Chrome or Edge.
- **Alternate:** Open `\\intweb.itu.int\intwebroot\ITU-R\space\css\notification\index_api.html` from Windows Explorer.

On first load the page fetches live data from the backend. If the backend is unreachable, a **yellow banner** will appear showing the timestamp of the last successful fetch, and the table will be populated from the offline backup cache (`frontend/data/cached_data.json`).

### 4.2 Filtering Data

**Top filter bar (main filters):**

| Control | Description |
|---------|-------------|
| **Type of Submission** (chip selector) | Multi-select filter. Default: *NotifSS* and *ResubSS*. Click the box to open the dropdown. Click a chip's **×** to remove it. Click the right-side **×** to clear all. |
| **Status** | Single-select dropdown. Filters by current notice status. |
| **BRReg date range** | Date range filter on the BR registration date column. |

**In-table column filters (in the table header row):**
Each column has its own filter input directly below the column header. Text columns support substring search; date columns have a date picker; TypeOfSubmission and Status have multi-select checkboxes.

### 4.3 Searching

Use the **Search** box (top right) for a global text search across all visible columns.

### 4.4 Refreshing Data

Click the **Refresh** button to fetch the latest data from the backend databases. This triggers a full re-query of both the Access MDB and SQL Server. Progress is shown by a loading overlay.

### 4.5 Resetting Filters

Click the **Reset** button to restore all filters to their defaults:
- TypeOfSubmission → NotifSS + ResubSS
- All other filters → cleared / set to "All"

### 4.6 Exporting Data

Click the **Export** button to download the currently **visible/filtered** rows as an Excel (`.xlsx`) file.

### 4.7 Column Visibility

Use the column chip toggles at the top of the page to show or hide individual columns. Click **Apply** to apply changes. Column settings are saved in browser `localStorage` and restored on next visit. Click **Reset** (next to Apply) to clear saved settings and restore defaults.

---

## 5. TypeOfSubmission Values

| Short Name | Full Name |
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

## 6. Offline / Backup Cache

After every successful data fetch the backend writes a backup file:

```
TrackingNotif/frontend/data/cached_data.json
```

Format:
```json
{
  "generated_at": "2025-01-15T09:30:00.000000",
  "data": [ ... ]
}
```

If the backend is unavailable, the frontend automatically falls back to this file and displays an **orange banner** with the cache timestamp. The table is fully functional (filtering, export, search) with cached data.

---

## 7. Maintenance Guide

### 7.1 Starting the Backend

On the server (`156.106.168.185`):

```bat
cd C:\path\to\TrackingNotif\backend
python api.py
```

Or use the provided batch script:

```bat
TrackingNotif\backend\start_api.bat
```

The server starts on `https://0.0.0.0:5001` and listens on all network interfaces.

### 7.2 Stopping the Backend

Press `Ctrl+C` in the terminal running `api.py`.

If the process was left running in the background and port 5001 is in use:

```powershell
# Find the PID using port 5001
netstat -ano | findstr :5001
# Kill the process
taskkill /PID <PID> /F
```

### 7.3 Verifying the Backend Is Running

```powershell
Invoke-WebRequest -Uri "https://156.106.168.185:5001/api/health" -SkipCertificateCheck
```

Expected response: `{"status": "ok"}`

### 7.4 SSL Certificate

The self-signed certificate files are located at:

```
TrackingNotif/backend/cert.pem
TrackingNotif/backend/key.pem
```

The certificate covers:
- IP: `156.106.168.185`
- `localhost`
- `127.0.0.1`
- Valid for **10 years**

To regenerate the certificate (when it expires or the server IP changes):

```bat
cd TrackingNotif\backend
python generate_cert.py
```

After regeneration, users must re-trust the certificate in their browsers (see Section 3).

### 7.5 Updating the Backend IP

If the server IP changes, update the following:

1. Regenerate the SSL certificate for the new IP (see 7.4).
2. In `TrackingNotif/frontend/index_api.html` and `TrackingNotif_edited/frontend/index_api.html`, update:
   ```javascript
   const BACKEND_URL = 'https://<NEW_IP>:5001';
   ```
3. Update the cert-trust banner link in the HTML:
   ```html
   <a href="https://<NEW_IP>:5001/api/health" ...>
   ```

### 7.6 Data Source Configuration

Database connection settings are in `TrackingNotif/backend/api.py`. **Credentials can now be set via environment variables** (recommended for security), with hardcoded fallback values:

| Environment Variable | Description | Fallback Default |
|----------------------|-------------|------------------|
| `MDB_USER` | MS Access username | `spruser01` |
| `MDB_PASSWORD` | MS Access password | `spruser01` |
| `SQL_SERVER_USER` | SQL Server username | `sns_a` |
| `SQL_SERVER_PASSWORD` | SQL Server password | `sns_a_5678` |
| `SQL_SERVER_HOST` | SQL Server hostname | `sydney.itu.int` |
| `SQL_SERVER_DB` | SQL Server database name | `SpaceNetworkSystem` |

All MDB connections use `ReadOnly=1` so the database can be opened simultaneously by multiple users without locking.

### 7.7 CORS Configuration

The backend restricts CORS (Cross-Origin Resource Sharing) to a known set of origins for security. When the frontend is served from a different origin (e.g., IIS at `https://intweb.itu.int`), the browser sends a cross-origin request that must be allowed.

**Allowed origins (built-in):**
- `https://156.106.168.185:5001` / `http://156.106.168.185:5001`
- `https://localhost:5001` / `http://localhost:5001`
- `https://127.0.0.1:5001` / `http://127.0.0.1:5001`
- `https://intweb.itu.int` / `http://intweb.itu.int`
- `null` (for `file://` access)

**Adding additional origins:** Set the `FRONTEND_ORIGIN` environment variable with comma-separated URLs:
```bat
set FRONTEND_ORIGIN=https://example.com,https://another.example.com
```

If the frontend shows "Offline – using backup data" on other machines despite the backend running, this is likely a CORS issue — verify the accessing origin is in the allowed list.

### 7.8 Security Configuration

**Rate limiting:** The `/api/data` endpoint is rate-limited to **30 requests per 60 seconds** per client IP to prevent abuse.

**SSL private key:** The file `backend/key.pem` is stored without encryption. On Windows, restrict NTFS permissions to Administrators only. On Linux, use `chmod 600 key.pem`.

**Database credentials:** See Section 7.6 — use environment variables to avoid storing passwords in source code.

### 7.9 Installing Dependencies

```bat
cd TrackingNotif\backend
pip install -r requirements.txt
```

Key dependencies: `flask`, `pyodbc`, `cryptography`

The MS Access driver must also be installed: **Microsoft Access Database Engine 2016 Redistributable** (64-bit).

### 7.10 Troubleshooting

| Symptom | Likely cause | Fix |
|---------|-------------|-----|
| "Connection failed" banner in browser | Backend not running | Start `api.py` (Section 7.1) |
| Certificate warning / blocked connection | Certificate not trusted | Follow Section 3 |
| Table shows "Offline" with orange banner on other machines | CORS origin not allowed | Verify origin in allowed list (Section 7.7) or set `FRONTEND_ORIGIN` env var |
| Table shows stale data with orange banner | Backend unreachable; cached data displayed | Check backend status |
| Port 5001 already in use | Old process still running | Kill the old process (Section 7.2) |
| No data after Refresh | Database path `M:\` not accessible | Verify drive mapping on the server machine |
| Export button produces empty file | All rows filtered out | Reset filters first |
| 429 Too Many Requests error | Rate limit exceeded | Wait and retry (limit: 30 req/min) |

---

## 8. Repository

- **Git remote:** `https://github.com/Infinite-Ng/TrackingNotif.git`
- **Main branch:** `main`

---

*Last updated: June 2026*
