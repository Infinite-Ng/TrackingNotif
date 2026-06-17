# ESIM Resolution Classification (RES156 / RES169 / RES123)

## Overview

The RES156, RES169, and RES123 columns indicate whether a space-network notice qualifies under specific ITU World Radiocommunication Conference (WRC) resolutions related to **Earth Stations in Motion (ESIM)** communicating with space stations in specific frequency bands.

The classification is performed by querying the **SRS database** (`srs_all.mdb`) and applying rule-based logic to each group/beam record.

---

## Data Flow

```
SRS Database (srs_all.mdb)                   Main Data (sntrdat.mdb + SQL Server)
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
| 1 | `e_srvcls` INNER JOIN `grp` | Get all ESIM groups with their service class (`stn_cls`), frequency range (`freq_min`, `freq_max`), and `ntc_id` |
| 2 | `notice` | Look up `ntc_type` (G = GSO, N = Non-GSO) for each `ntc_id` from Step 1, batched in groups of 500 |
| 3 | (in-memory) | Classify each group row individually, then aggregate by `ntc_id` (if ANY group for a notice matches a resolution, that resolution is `True`) |

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
SRS Step 1: <N> group rows with ESIM classes     ← Total ESIM groups found
SRS Step 2: <M> ntc_ids with ntc_type            ← How many have orbit type
ESIM classification complete: <K> ntc_ids        ← Unique notices classified
ESIM merge: <matched>/<total> records            ← How many main-data notices matched
```

---

## Source Code References

| Component | File | Line(s) |
|-----------|------|---------|
| Resolution rules | `backend/api.py` | 132–167 |
| Frequency overlap check | `backend/api.py` | 170–174 |
| Single group classifier | `backend/api.py` | 177–191 |
| ESIM fetch & classify | `backend/api.py` | 193–267 |
| Merge into main data | `backend/api.py` | 512–524 |
