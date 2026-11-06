# ESIM Resolution Classification (RES156 / RES169 / RES123)

## Overview

The RES156, RES169, and RES123 columns indicate whether a space-network notice qualifies under specific ITU World Radiocommunication Conference (WRC) resolutions related to **Earth Stations in Motion (ESIM)** communicating with space stations in specific frequency bands.

The classification is performed by querying the **live SNS database** (SQL Server `sydney.itu.int` / `SpaceNetworkSystem`, schema `dbo`, SELECT only) and applying rule-based logic to each group/beam record. Until October 2026 it read `srs_all.mdb`, the weekly copy of the same tables; the rules did not change.

Unlike Res35 (`dbo.grp_res35`), the SNS database has no table with ready-made ESIM results, so the classification is always rule-based.

---

## Data Flow

```
SNS database (SQL Server, dbo)               Main Data (sntrdat.mdb + SQL Server)
┌─────────────────────────────┐              ┌──────────────────────────────┐
│ e_srvcls (service classes)  │              │ final_data[]                 │
│   └─ grp (groups/beams)     │              │   ├─ ntc_id                  │
│       ├─ stn_cls            │              │   ├─ RES156 ← True/False     │
│       ├─ freq_min / freq_max│  ──match──▶  │   ├─ RES169 ← True/False     │
│       └─ ntc_id             │   on ntc_id  │   └─ RES123 ← True/False     │
│   └─ notice (orbit type)    │              │                              │
│       ├─ ntc_id             │              └──────────────────────────────┘
│       └─ ntc_type (G / N)   │
└─────────────────────────────┘
```

### Query Steps (in `fetch_esim_resolutions()`)

| Step | Table(s) | Purpose |
|------|----------|---------|
| 1 | `dbo.e_srvcls` ⋈ `dbo.grp` ⋈ `dbo.notice` | One query: all distinct ESIM rows (`stn_cls` in UF/UC/UO/US/UU) with frequency range (`freq_min`, `freq_max`), `ntc_id` and `ntc_type` (G = GSO, N = Non-GSO) |
| 2 | (in-memory) | Classify each row, then aggregate by `ntc_id` (if ANY group for a notice matches a resolution, that resolution is `True`) |

The whole classification takes about 0.5 s (about 17 s when it read `srs_all.mdb`).

### Popup details (`fetch_esim_groups()`)

`GET /api/details/<ntc_id>` returns, for each resolution, whether the notice qualifies and the matching groups (`grp_id`, beam, E/R, frequency range, ESIM class, matching band). The popup's **Resolutions** block shows a Yes/No row per resolution; **Groups ▸** opens the group list.

### Merge (in `fetch_data()`)

After classification, each row in the main dataset is matched by `ntc_id` against the classification results. Unmatched notices default to `RES156=False, RES169=False, RES123=False`.

---

## Classification Rules

Each group/beam record is evaluated independently against all three resolution rules. A single group matching a resolution makes the entire notice qualify for that resolution.

### General Logic (`_classify_single()`)

```
For each resolution rule:
  1. Check service class:  stn_cls ∈ rule.classes
  2. Check orbit type:     ntc_type == rule.orbit
  3. Check frequency:      [freq_min, freq_max] overlaps with any band in rule.freq_bands

  If ALL three conditions are met → RESxxx = True
```

> **Note on frequency direction:** The `emi_rcp` field (R = receive / E = emit) is deliberately **NOT used** in classification, because the database direction may differ from the earth-station perspective used in the ITU technical specifications.

### Frequency Overlap Check (`_freq_overlaps_any()`)

```python
def _freq_overlaps_any(freq_min, freq_max, bands):
    for (lo, hi) in bands:
        if freq_min <= hi and freq_max >= lo:
            return True
    return False
```

Two frequency ranges [min₁, max₁] and [min₂, max₂] overlap if and only if:
$$min_1 \leq max_2 \quad\text{AND}\quad max_1 \geq min_2$$

---

## Resolution-Specific Rules

### RES156 (WRC-15)

| Criterion | Value |
|-----------|-------|
| **Service Classes** | `UF` (Unplanned Fixed), `UC` (Unplanned Coordination) |
| **Orbit** | `G` (Geostationary / GSO) |
| **Frequency Bands** | 19.7 – 20.2 GHz (space-to-earth) |
| | 29.5 – 30.0 GHz (earth-to-space) |

### RES169 (WRC-19)

| Criterion | Value |
|-----------|-------|
| **Service Classes** | `UO` (Unplanned Other), `US` (Unplanned Standard), `UU` (Unplanned Unspecified) |
| **Orbit** | `G` (Geostationary / GSO) |
| **Frequency Bands** | 17.7 – 19.7 GHz (space-to-earth) |
| | 27.5 – 29.5 GHz (earth-to-space) |

### RES123 (WRC-19)

| Criterion | Value |
|-----------|-------|
| **Service Classes** | `UO` (Unplanned Other), `US` (Unplanned Standard) |
| **Orbit** | `N` (Non-Geostationary / Non-GSO) |
| **Frequency Bands** | 17.7 – 18.6 GHz (space-to-earth) |
| | 18.8 – 19.3 GHz (space-to-earth) |
| | 19.7 – 20.2 GHz (space-to-earth) |
| | 27.5 – 29.1 GHz (earth-to-space) |
| | 29.5 – 30.0 GHz (earth-to-space) |

---

## Data Expectations

### Why certain resolutions may show no data

Because the rules use logical **AND** across three criteria, a notice only qualifies if it simultaneously satisfies the service class, orbit type, AND frequency requirements:

| Scenario | RES156 | RES169 | RES123 |
|----------|--------|--------|--------|
| GSO notice, class UF/UC, freq 19.7–20.2 GHz | ✅ | ❌ (wrong class) | ❌ (not Non-GSO) |
| GSO notice, class UO/US/UU, freq 17.7–19.7 GHz | ❌ (wrong class) | ✅ | ❌ (not Non-GSO) |
| Non-GSO notice, class UO/US, freq 19.7–20.2 GHz | ❌ (wrong class) | ❌ (not GSO) | ✅ |

- **RES169 requires classes UO/US/UU** — if most SRS groups are UF/UC (fixed satellite service), RES169 will be mostly empty.
- **RES123 requires Non-GSO orbit** — the vast majority of space-network notices are GSO, so RES123 will naturally have very few matches.

### Debugging

Check the Flask backend console for these log lines:

```
ESIM classification: <N> ESIM group rows, <K> notices RES156=.. RES169=.. RES123=..
ESIM merge: <matched>/<total> records have ESIM groups in SNS (RES156=.. RES169=.. RES123=..)
```

---

## Source Code References (`backend/api.py`)

| Component | Name |
|-----------|------|
| Resolution rules | `_RES_RULES` |
| Frequency overlap check | `_freq_overlaps_any()` |
| Single group classifier | `_classify_single()` |
| Classification for the main table | `fetch_esim_resolutions()` |
| Matching groups for the popup | `fetch_esim_groups()` |
| Merge into main data | `fetch_data()` |
