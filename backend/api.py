"""
Flask API Backend for ITU Notice Tracking System
Provides real-time data from SQL Server and MS Access databases
"""
import json
import os
import shutil
import time
import pyodbc
from dotenv import load_dotenv
from flask import Flask, jsonify, send_from_directory, request
from flask_cors import CORS
from datetime import date, datetime, timezone
import logging
import threading
from functools import wraps

# Load .flaskenv / .env before reading os.environ
load_dotenv(dotenv_path=os.path.join(os.path.dirname(os.path.abspath(__file__)), '.flaskenv'))

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# --- Simple in-memory rate limiter ---
# Tracks request timestamps per client IP; allows max_requests per window_seconds.
_rate_limit_store = {}  # {ip: [timestamp, ...]}
_RATE_LIMIT_WINDOW = 60  # seconds
_RATE_LIMIT_MAX = 30  # max requests per window per IP


def rate_limit(f):
    """Decorator: limit requests per IP within a rolling time window."""

    @wraps(f)
    def wrapper(*args, **kwargs):
        client_ip = request.remote_addr or '127.0.0.1'
        now = time.time()
        timestamps = _rate_limit_store.get(client_ip, [])
        # Purge expired entries
        timestamps = [t for t in timestamps if now - t < _RATE_LIMIT_WINDOW]
        if len(timestamps) >= _RATE_LIMIT_MAX:
            logger.warning(f"Rate limit exceeded for {client_ip}")
            return jsonify({
                'success': False,
                'error': 'Too many requests. Please try again later.'
            }), 429
        timestamps.append(now)
        _rate_limit_store[client_ip] = timestamps
        return f(*args, **kwargs)

    return wrapper


app = Flask(__name__)

# --- CORS Configuration ---
# This is an internal ITU tool; frontends may be served from Flask itself
# (same-origin, no CORS needed) or from the IIS intweb server (cross-origin).
_ALLOWED_ORIGINS = [
    # Flask self-served origins (same-origin, included for completeness)
    'https://156.106.168.185:5001',
    'http://156.106.168.185:5001',
    'https://localhost:5001',
    'http://localhost:5001',
    'https://127.0.0.1:5001',
    'http://127.0.0.1:5001',
    # IIS intweb frontend (cross-origin)
    'https://intweb.itu.int',
    'http://intweb.itu.int',
    # Allow file:// origins (some browsers send Origin: null)
    'null',
]
# Also allow additional origins via environment variable (comma-separated)
_extra_origins = os.environ.get('FRONTEND_ORIGIN')
if _extra_origins:
    for origin in _extra_origins.split(','):
        origin = origin.strip()
        if origin and origin not in _ALLOWED_ORIGINS:
            _ALLOWED_ORIGINS.append(origin)

CORS(app, origins=_ALLOWED_ORIGINS, supports_credentials=False)

# --- Frontend directory ---
FRONTEND_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'frontend')

# --- Configuration ---
# Network source paths – use UNC so the admin-elevated process can reach them
# (mapped drive letters like M: are not visible in elevated sessions)
_NET_MDB = r'\\blue\dfs\br\BR_DATA\SPACE\SNTRACK\sntrdat.mdb'
_NET_MDW = r'\\blue\dfs\br\BR_DATA\SPACE\SNTRACK\sntrapp.mdw'


def _require_secret(env_name):
    """Read a secret from the environment. Returns '' if unset and logs a clear
    warning, so the app starts but DB connections fail loudly rather than
    silently using a hardcoded password baked into the source."""
    value = os.environ.get(env_name)
    if not value:
        logger.warning(
            f"Environment variable {env_name} is not set. Database connections "
            f"requiring it will fail. Set it before starting (see .flaskenv.example)."
        )
        return ''
    return value


MDB_USERNAME = os.environ.get('MDB_USER', 'spruser01')
MDB_PASSWORD = _require_secret('MDB_PASSWORD')

# Local data directory – files here are used for all connections.
# Manually pre-seeded; auto-refreshed on every /api/data request.
LOCAL_DATA_DIR = r'C:\Users\xuzhi\TrackingNotif\data'
LOCAL_MDB_PATH = os.path.join(LOCAL_DATA_DIR, 'sntrdat.mdb')
LOCAL_MDW_PATH = os.path.join(LOCAL_DATA_DIR, 'sntrapp.mdw')


def _sync_local_files():
    """Try to copy the latest MDB and MDW files from the network to LOCAL_DATA_DIR.
    Falls back silently if the network copy is locked or unreachable – the
    existing local copies will be used instead."""
    os.makedirs(LOCAL_DATA_DIR, exist_ok=True)
    for src, dst, label in [
        (_NET_MDB, LOCAL_MDB_PATH, 'MDB'),
        (_NET_MDW, LOCAL_MDW_PATH, 'MDW'),
    ]:
        try:
            shutil.copy2(src, dst)
            logger.info(f"{label} synced to local: {dst}")
        except Exception as e:
            logger.warning(f"Could not sync {label} from network ({e}); using existing local copy.")


SQL_SERVER_CONN_STRING = (
    "DRIVER={SQL Server};"
    f"SERVER={os.environ.get('SQL_SERVER_HOST', 'sydney.itu.int')};"
    f"DATABASE={os.environ.get('SQL_SERVER_DB', 'SpaceNetworkSystem')};"
    f"UID={os.environ.get('SQL_SERVER_USER', 'css_sns_ro_notif')};"
    f"PWD={_require_secret('SQL_SERVER_PASSWORD')};"
)

# --- SRS Access file (weekly copy of the SNS tables) ---
# The backend reads the live SNS database; srs_all.mdb is only used by
# tools/compare_coord_stats.py (--source-srs).
# Use UNC path so the admin-elevated process can reach it
# (mapped drive M: is NOT visible in elevated sessions)
_SRS_MDB_PATH = os.environ.get('SRS_MDB_PATH', r'\\blue\dfs\br\BR_DATA\SPACE\SRS_DB\srs_all.mdb')

# Resolution classification rules (frequencies in MHz)
# Each resolution has ALL applicable frequency bands combined — we do NOT
# key by emi_rcp (R/E) because the database direction may differ from the
# earth-station perspective used in the technical specification.
_RES_RULES = {
    'RES156': {
        'classes': {'UF', 'UC'},
        'orbit': 'G',  # ntc_type = 'G' (GSO)
        'freq_bands': [
            (19700, 20200),  # Downlink: 19.7-20.2 GHz
            (29500, 30000),  # Uplink:   29.5-30.0 GHz
        ],
    },
    'RES169': {
        'classes': {'UO', 'US', 'UU'},
        'orbit': 'G',
        'freq_bands': [
            (17700, 19700),  # Downlink: 17.7-19.7 GHz
            (27500, 29500),  # Uplink:   27.5-29.5 GHz
        ],
    },
    'RES123': {
        'classes': {'UO', 'US'},
        'orbit': 'N',  # Non-GSO
        'freq_bands': [
            (17700, 18600), (18800, 19300), (19700, 20200),  # Downlink
            (27500, 29100), (29500, 30000),  # Uplink
        ],
    },
}


def _freq_overlaps_any(freq_min, freq_max, bands):
    """Return True if [freq_min, freq_max] overlaps with any of the given frequency bands."""
    for (lo, hi) in bands:
        if freq_min <= hi and freq_max >= lo:
            return True
    return False


def _classify_single(e_srvcls, freq_min, freq_max, ntc_type):
    """Classify a single group/beam record into applicable resolutions.

    Returns a dict like {'RES156': True, 'RES169': False, 'RES123': False}.
    Frequency bands are checked without regard to emi_rcp direction.
    """
    result = {'RES156': False, 'RES169': False, 'RES123': False}
    if e_srvcls is None or freq_min is None or freq_max is None:
        return result
    e_srvcls = str(e_srvcls).strip().upper()
    if ntc_type:
        ntc_type = str(ntc_type).strip().upper()

    for res_key, rule in _RES_RULES.items():
        if e_srvcls not in rule['classes']:
            continue
        if ntc_type != rule['orbit']:
            continue
        if _freq_overlaps_any(freq_min, freq_max, rule['freq_bands']):
            result[res_key] = True
    return result


def _connect_srs(mdb_path=None):
    """Open a READ-ONLY connection to an SRS-format Access database
    (srs_all.mdb by default). Only SELECT statements may ever be executed on it."""
    conn_str = (
        r'DRIVER={Microsoft Access Driver (*.mdb, *.accdb)};'
        f'DBQ={mdb_path or _SRS_MDB_PATH};'
        r'ReadOnly=1;'
    )
    return pyodbc.connect(conn_str, readonly=True)


def _clean(value):
    """Strip a DB value; NULL / blank becomes None."""
    if value is None:
        return None
    value = str(value).strip()
    return value or None


def _summarize_coordination(rows):
    """Summarize distinct (coord_prov, agree_st, coord_st, adm) rows of provn or
    tr_aff_ntw, following Wu's coord_st statistics definitions (e-mail 2026-02-24).

    Administrations are de-duplicated: an adm appearing on many rows counts once.
      agree_st          {symbol: [adm, ...]}  count = len(list); 'O' = Excel J/L
      coord_st          {symbol: [adm, ...]}  'O' = Excel K/O
      coord_st_details  {coord_st symbol: {agree_st symbol: [adm, ...]}}
                        ['O'] = Excel N coord_st_o_details
      coord_prov_values sorted distinct coord_prov (Excel M / Q)
    A symbol whose rows all have a NULL adm is kept with an empty list (count 0).
    """
    agree, coord, details, provs = {}, {}, {}, set()
    for coord_prov, agree_st, coord_st, adm in rows:
        coord_prov, agree_st, coord_st, adm = map(_clean, (coord_prov, agree_st, coord_st, adm))
        if coord_prov:
            provs.add(coord_prov)
        if agree_st:
            adms = agree.setdefault(agree_st, set())
            if adm:
                adms.add(adm)
        if coord_st:
            adms = coord.setdefault(coord_st, set())
            if adm:
                adms.add(adm)
            sub = details.setdefault(coord_st, {}).setdefault(agree_st or '', set())
            if adm:
                sub.add(adm)

    def as_lists(d):
        return {k: sorted(v) for k, v in sorted(d.items())}

    return {
        'has_data': bool(rows),
        'coord_prov_values': sorted(provs),
        'agree_st': as_lists(agree),
        'coord_st': as_lists(coord),
        'coord_st_details': {k: as_lists(v) for k, v in sorted(details.items())},
    }


def _query_coordination(cursor, ntc_id, schema=''):
    """Coordination statistics of one notice (SELECT only).

    Works on both copies of the SRS table structure:
      - SNS database on SQL Server, schema 'dbo.' (live data, used by the popup)
      - SRS-format Access files ('' schema): srs_all.mdb (updated weekly) or a
        submitted MDB (used by tools/compare_coord_stats.py)
    Table links:
      notice          -> ntc_id
      provn           -> grp_id -> grp.ntc_id
      tr_aff_ntw      -> ntc_id
      coord_agree_ntw -> ntc_id
      all_aff_ntw     -> aff_rec_id -> grp_aff_rec.grp_id -> grp.ntc_id
    Returns None for every field when the notice is not found.
    """
    s = schema
    details = {
        'found': False,
        'prov': None,
        'ntf_occurs': None,
        'prov_desc': None,
        'provn': None,
        'tr_aff_ntw': None,
        'coord_agree_ntw_count': None,
        'all_aff_ntw_count': None,
    }
    cursor.execute(f"SELECT prov, ntf_occurs, prov_desc FROM {s}[notice] WHERE ntc_id = ?", ntc_id)
    row = cursor.fetchone()
    if row is None:
        return details
    details['found'] = True
    details['prov'], details['ntf_occurs'], details['prov_desc'] = row[0], row[1], row[2]

    cursor.execute(f"""
        SELECT DISTINCT p.coord_prov, p.agree_st, p.coord_st, p.adm
        FROM {s}[provn] AS p INNER JOIN {s}[grp] AS g ON p.grp_id = g.grp_id
        WHERE g.ntc_id = ?
    """, ntc_id)
    details['provn'] = _summarize_coordination(cursor.fetchall())

    # tr_aff_ntw is often empty (cleared after the coordination wizard runs).
    cursor.execute(f"""
        SELECT DISTINCT coord_prov, agree_st, coord_st, adm
        FROM {s}[tr_aff_ntw]
        WHERE ntc_id = ?
    """, ntc_id)
    details['tr_aff_ntw'] = _summarize_coordination(cursor.fetchall())

    # No primary key on coord_agree_ntw: plain row count, no de-duplication.
    cursor.execute(f"SELECT COUNT(*) FROM {s}[coord_agree_ntw] WHERE ntc_id = ?", ntc_id)
    details['coord_agree_ntw_count'] = cursor.fetchone()[0]

    # One all_aff_ntw record can be linked to several groups of the same notice,
    # so count distinct aff_rec_id (written without COUNT(DISTINCT) for Access).
    cursor.execute(f"""
        SELECT COUNT(*) FROM (
            SELECT DISTINCT a.aff_rec_id
            FROM ({s}[all_aff_ntw] AS a
                  INNER JOIN {s}[grp_aff_rec] AS r ON a.aff_rec_id = r.aff_rec_id)
                  INNER JOIN {s}[grp] AS g ON r.grp_id = g.grp_id
            WHERE g.ntc_id = ?
        ) AS x
    """, ntc_id)
    details['all_aff_ntw_count'] = cursor.fetchone()[0]
    return details


def fetch_notice_details(ntc_id, conn):
    """Popup coordination statistics from the live SNS database (SQL Server, dbo)."""
    return _query_coordination(conn.cursor(), ntc_id, schema='dbo.')


def fetch_notice_details_mdb(ntc_id, mdb_path=None):
    """Same statistics from an SRS-format Access file (srs_all.mdb by default)."""
    conn = _connect_srs(mdb_path)
    try:
        return _query_coordination(conn.cursor(), ntc_id)
    finally:
        conn.close()


_ESIM_KEYS = ('RES156', 'RES169', 'RES123')
_ESIM_CLASSES_SQL = "('UF', 'UC', 'UO', 'US', 'UU')"


def fetch_esim_resolutions():
    """Classify each ntc_id for RES156/RES169/RES123 from the live SNS database
    (SQL Server, dbo; SELECT only). One set-based query, rules in _RES_RULES.
    Returns a dict: {ntc_id_str: {'RES156': bool, 'RES169': bool, 'RES123': bool}}
    """
    result = {}
    conn = get_sql_server_connection()
    if not conn:
        logger.warning("SNS database not reachable; skipping ESIM classification.")
        return result
    try:
        cursor = conn.cursor()
        cursor.execute(f"""
            SELECT DISTINCT e.stn_cls, g.ntc_id, g.freq_min, g.freq_max, n.ntc_type
            FROM dbo.e_srvcls e
            INNER JOIN dbo.grp g ON e.grp_id = g.grp_id
            INNER JOIN dbo.notice n ON n.ntc_id = g.ntc_id
            WHERE e.stn_cls IN {_ESIM_CLASSES_SQL}
        """)
        rows = cursor.fetchall()
        for stn_cls, ntc_id, freq_min, freq_max, ntc_type in rows:
            flags = result.setdefault(str(ntc_id), {k: False for k in _ESIM_KEYS})
            for res_key, hit in _classify_single(stn_cls, freq_min, freq_max, ntc_type).items():
                flags[res_key] = flags[res_key] or hit
        logger.info(f"ESIM classification: {len(rows)} ESIM group rows, {len(result)} notices"
                    + ''.join(f" {k}={sum(1 for v in result.values() if v[k])}" for k in _ESIM_KEYS))
    except Exception as e:
        logger.error(f"Error fetching ESIM resolutions: {e}")
    finally:
        conn.close()
    return result

def fetch_esim_groups(ntc_id, conn):
    """Groups of one notice that make it RES156 / RES169 / RES123 (popup details)."""
    cursor = conn.cursor()
    cursor.execute(f"""
        SELECT DISTINCT g.grp_id, g.beam_name, g.emi_rcp, g.freq_min, g.freq_max, e.stn_cls, n.ntc_type
        FROM dbo.e_srvcls e
        INNER JOIN dbo.grp g ON e.grp_id = g.grp_id
        INNER JOIN dbo.notice n ON n.ntc_id = g.ntc_id
        WHERE g.ntc_id = ? AND e.stn_cls IN {_ESIM_CLASSES_SQL}
    """, ntc_id)
    result = {k: {'is_res': False, 'groups': []} for k in _ESIM_KEYS}
    groups = {}
    for grp_id, beam, emi_rcp, freq_min, freq_max, stn_cls, ntc_type in cursor.fetchall():
        stn_cls = _clean(stn_cls)
        for res_key, hit in _classify_single(stn_cls, freq_min, freq_max, ntc_type).items():
            if not hit:
                continue
            g = groups.setdefault((res_key, grp_id), {
                'grp_id': grp_id, 'beam_name': _clean(beam), 'emi_rcp': _clean(emi_rcp),
                'freq_min': freq_min, 'freq_max': freq_max, 'stn_cls': [],
                'bands': [f'{lo / 1000:.2f}-{hi / 1000:.2f} GHz'
                          for lo, hi in _RES_RULES[res_key]['freq_bands']
                          if freq_min <= hi and freq_max >= lo]})
            if stn_cls not in g['stn_cls']:
                g['stn_cls'].append(stn_cls)
    for (res_key, _), g in sorted(groups.items(), key=lambda kv: kv[0][1]):
        result[res_key]['groups'].append(g)
        result[res_key]['is_res'] = True
    return result


# --- Resolution 35 (milestone-based approach for non-GSO systems) ---
# Both options read the SNS database (SQL Server SpaceNetworkSystem, schema dbo).
#   Option 2: dbo.grp_res35 lists every group (beam) handled under Res35.
#   Option 1: re-derive from resolves 1 of Res 35 (Rev.WRC-23): frequency assignments
#             to non-GSO systems brought into use (Nos. 11.44 / 11.44C) in the bands and
#             services of the Res 35 Table.
# A notice is Res35 as soon as one of its groups is.
_RES35_S_E, _RES35_E_S = 'space-to-Earth', 'Earth-to-space'
_RES35_FSS, _RES35_MSS, _RES35_BSS = 'FSS', 'MSS', 'BSS'

_RES35_BOTH = (_RES35_S_E, _RES35_E_S)   # no direction in the Table = both directions


def _res35_entry(*by_region):
    """Build {region: {(service, direction), ...}} from (regions, service, directions) items."""
    out = {1: set(), 2: set(), 3: set()}
    for regions, service, directions in by_region:
        for r in regions:
            out[r] |= {(service, d) for d in directions}
    return out


_R123, _R1, _R2, _R3 = (1, 2, 3), (1,), (2,), (3,)
_SE, _ES = (_RES35_S_E,), (_RES35_E_S,)
# Res 35 (Rev.WRC-23) Table, frequencies in GHz, services per ITU Region.
_RES35_TABLE = [
    (10.70, 11.70, _res35_entry((_R123, _RES35_FSS, _SE), (_R1, _RES35_FSS, _ES))),
    (11.70, 12.50, _res35_entry((_R123, _RES35_FSS, _SE))),
    (12.50, 12.70, _res35_entry((_R123, _RES35_FSS, _SE), (_R1, _RES35_FSS, _ES), (_R3, _RES35_BSS, _RES35_BOTH))),
    (12.70, 12.75, _res35_entry((_R1 + _R3, _RES35_FSS, _SE), (_R1 + _R2, _RES35_FSS, _ES),
                                (_R3, _RES35_BSS, _RES35_BOTH))),
    (12.75, 13.25, _res35_entry((_R123, _RES35_FSS, _ES))),
    (13.75, 14.50, _res35_entry((_R123, _RES35_FSS, _ES))),
    (17.30, 17.70, _res35_entry((_R1, _RES35_FSS, _SE), (_R1 + _R3, _RES35_FSS, _ES))),
    (17.70, 17.80, _res35_entry((_R123, _RES35_FSS, _SE), (_R1 + _R3, _RES35_FSS, _ES))),
    (17.80, 18.10, _res35_entry((_R123, _RES35_FSS, _RES35_BOTH))),
    (18.10, 19.30, _res35_entry((_R123, _RES35_FSS, _SE))),
    (19.30, 19.60, _res35_entry((_R123, _RES35_FSS, _RES35_BOTH))),
    (19.60, 19.70, _res35_entry((_R123, _RES35_FSS, _RES35_BOTH))),
    (19.70, 20.10, _res35_entry((_R123, _RES35_FSS, _SE), (_R123, _RES35_MSS, _SE))),
    (20.10, 20.20, _res35_entry((_R123, _RES35_FSS, _SE), (_R123, _RES35_MSS, _SE))),
    (27.00, 27.50, _res35_entry((_R2 + _R3, _RES35_FSS, _ES))),
    (27.50, 29.50, _res35_entry((_R123, _RES35_FSS, _ES))),
    (29.50, 29.90, _res35_entry((_R123, _RES35_FSS, _ES), (_R123, _RES35_MSS, _ES))),
    (29.90, 30.00, _res35_entry((_R123, _RES35_FSS, _ES), (_R123, _RES35_MSS, _ES))),
    (37.50, 38.00, _res35_entry((_R123, _RES35_FSS, _SE))),
    (38.00, 39.50, _res35_entry((_R123, _RES35_FSS, _SE))),
    (39.50, 40.50, _res35_entry((_R123, _RES35_FSS, _SE), (_R123, _RES35_MSS, _SE))),
    (40.50, 42.50, _res35_entry((_R123, _RES35_FSS, _SE), (_R123, _RES35_BSS, _RES35_BOTH))),
    (47.20, 50.20, _res35_entry((_R123, _RES35_FSS, _ES))),
    (50.40, 51.40, _res35_entry((_R123, _RES35_FSS, _ES))),
]
# Service-area symbols that name a whole ITU Region (srv_area.ctry)
_REGION_OF_AREA_SYMBOL = {'XR1': 1, 'XR2': 2, 'XR3': 3}


def _res35_regions(service_area):
    """ITU Regions served by a group, from its service-area symbols.
    Returns (regions, determined): no service area = global (all Regions);
    country symbols only = Region not derivable from the data (all Regions, determined=False)."""
    regions = {_REGION_OF_AREA_SYMBOL[a] for a in service_area if a in _REGION_OF_AREA_SYMBOL}
    if regions:
        return regions, True
    return {1, 2, 3}, not service_area
# Space station class (srv_cls.stn_cls) -> service of the Res 35 Table
_RES35_SERVICE_OF_CLASS = {
    'EC': _RES35_FSS,                                                   # fixed-satellite
    'EI': _RES35_MSS, 'EJ': _RES35_MSS, 'EU': _RES35_MSS, 'EG': _RES35_MSS,  # mobile-satellite
    'EB': _RES35_BSS, 'EV': _RES35_BSS,                                 # broadcasting-satellite
}


def _res35_matching_bands(freq_min_mhz, freq_max_mhz, emi_rcp, station_classes, regions=(1, 2, 3)):
    """Res 35 Table rows ('lo-hi GHz service direction (Regions)') that a group falls in,
    in at least one of the given ITU Regions.
    emi_rcp is from the space station: E = space-to-Earth, R = Earth-to-space."""
    if freq_min_mhz is None or freq_max_mhz is None:
        return []
    direction = {'E': _RES35_S_E, 'R': _RES35_E_S}.get((emi_rcp or '').strip().upper())
    services = {_RES35_SERVICE_OF_CLASS[c] for c in station_classes if c in _RES35_SERVICE_OF_CLASS}
    hits = []
    for lo, hi, by_region in _RES35_TABLE:
        if freq_min_mhz < hi * 1000 and freq_max_mhz > lo * 1000:
            for service in sorted(services):
                where = sorted(r for r in regions if (service, direction) in by_region[r])
                if where:
                    label = '' if len(where) == 3 else ' (R' + ', R'.join(map(str, where)) + ')'
                    hits.append(f'{lo:.2f}-{hi:.2f} GHz {service} {direction}{label}')
    return hits

def _iso(value):
    return value.isoformat() if isinstance(value, (date, datetime)) else value


def fetch_res35(ntc_id, conn):
    """Res35 status of one notice from the SNS database (SELECT only)."""
    cursor = conn.cursor()
    cursor.execute("SELECT ntc_type FROM dbo.notice WHERE ntc_id = ?", ntc_id)
    row = cursor.fetchone()
    result = {'in_sns': row is not None, 'ntc_type': _clean(row[0]) if row else None}
    if row is None:
        return result

    cursor.execute("""
            SELECT grp_id, beam_name, emi_rcp, freq_min, freq_max, d_inuse
            FROM dbo.grp WHERE ntc_id = ?
        """, ntc_id)
    groups = {r[0]: {'grp_id': r[0], 'beam_name': _clean(r[1]), 'emi_rcp': _clean(r[2]),
                     'freq_min': r[3], 'freq_max': r[4], 'd_inuse': _iso(r[5]), 'stn_cls': []}
              for r in cursor.fetchall()}
    cursor.execute("""
            SELECT s.grp_id, s.stn_cls FROM dbo.srv_cls s
            INNER JOIN dbo.grp g ON g.grp_id = s.grp_id WHERE g.ntc_id = ?
        """, ntc_id)
    for grp_id, stn_cls in cursor.fetchall():
        stn_cls = _clean(stn_cls)
        if grp_id in groups and stn_cls and stn_cls not in groups[grp_id]['stn_cls']:
            groups[grp_id]['stn_cls'].append(stn_cls)
    cursor.execute("""
            SELECT r.grp_id, r.ms_step, r.f_ms_met, r.d_ms_next_deadline FROM dbo.grp_res35 r
            INNER JOIN dbo.grp g ON g.grp_id = r.grp_id WHERE g.ntc_id = ?
        """, ntc_id)
    milestones = {r[0]: {'ms_step': _clean(r[1]), 'f_ms_met': _clean(r[2]),
                         'd_ms_next_deadline': _iso(r[3])} for r in cursor.fetchall()}
    cursor.execute("""
            SELECT s.grp_id, s.ctry FROM dbo.srv_area s
            INNER JOIN dbo.grp g ON g.grp_id = s.grp_id WHERE g.ntc_id = ?
        """, ntc_id)
    for grp_id, ctry in cursor.fetchall():
        ctry = _clean(ctry)
        if grp_id in groups and ctry:
            groups[grp_id].setdefault('service_area', []).append(ctry)

    is_ngso = result['ntc_type'] == 'N'
    option1, option2 = [], []
    for grp_id in sorted(groups):
        g = groups[grp_id]
        g['service_area'] = sorted(set(g.get('service_area', [])))
        regions, determined = _res35_regions(g['service_area'])
        g['regions'] = sorted(regions)
        bands = _res35_matching_bands(g['freq_min'], g['freq_max'], g['emi_rcp'], g['stn_cls'], regions)
        # Flag only when the Region would change the result but cannot be read from the data
        per_region = [bool(_res35_matching_bands(g['freq_min'], g['freq_max'], g['emi_rcp'],
                                                 g['stn_cls'], (r,))) for r in (1, 2, 3)]
        g['region_check_needed'] = (not determined) and len(set(per_region)) > 1
        if is_ngso and g['d_inuse'] and bands:
            option1.append({**g, 'res35_bands': bands})
        if grp_id in milestones:
            option2.append({**g, **milestones[grp_id]})
    ids1, ids2 = {g['grp_id'] for g in option1}, {g['grp_id'] for g in option2}
    result.update({
        'option1': {'is_res35': bool(option1), 'groups': option1},
        'option2': {'is_res35': bool(option2), 'groups': option2},
        'compare': {'both': len(ids1 & ids2), 'only_option1': sorted(ids1 - ids2),
                    'only_option2': sorted(ids2 - ids1)},
    })
    return result


# --- Resolution 8 (orbital data of non-GSO space stations) ---
# dbo.res8_sp_stn holds Res 8 submissions: one row per space station, keyed by the
# submission's ntc_id; itu_sat_id starts with the ntc_id of the notice the
# satellite belongs to (e.g. '121500130_10_1' -> 121500130).
# A notice is Res8 when it is a Res 8 submission or is referenced by one.

def fetch_res8(ntc_id, conn):
    """Res 8 information of one notice (SELECT only)."""
    cursor = conn.cursor()
    cursor.execute("""
            SELECT itu_sat_id, sp_stn_name, res8_resolves FROM dbo.res8_sp_stn WHERE ntc_id = ?
            ORDER BY itu_sat_id
        """, ntc_id)
    own = [{'itu_sat_id': _clean(r[0]), 'sp_stn_name': _clean(r[1]), 'res8_resolves': _clean(r[2])}
           for r in cursor.fetchall()]
    cursor.execute("""
            SELECT ntc_id, COUNT(*) FROM dbo.res8_sp_stn
            WHERE itu_sat_id LIKE ? GROUP BY ntc_id ORDER BY ntc_id
        """, f'{ntc_id}[_]%')
    referenced_by = [{'ntc_id': r[0], 'stations': r[1]} for r in cursor.fetchall()]
    resolves = {}
    for s in own:
        key = s['res8_resolves'] or ''
        resolves[key] = resolves.get(key, 0) + 1
    return {'is_res8': bool(own or referenced_by), 'stations': own,
            'resolves': dict(sorted(resolves.items())), 'referenced_by': referenced_by}


def fetch_res8_notice_ids():
    """ntc_ids that are Res 8 submissions or are referenced by one (main table)."""
    conn = get_sql_server_connection()
    if not conn:
        return set()
    try:
        cursor = conn.cursor()
        cursor.execute("""
                SELECT ntc_id FROM dbo.res8_sp_stn
                UNION
                SELECT TRY_CAST(LEFT(itu_sat_id, CHARINDEX('_', itu_sat_id) - 1) AS int)
                FROM dbo.res8_sp_stn WHERE CHARINDEX('_', itu_sat_id) > 1
            """)
        return {str(r[0]) for r in cursor.fetchall() if r[0] is not None}
    except Exception as e:
        logger.error(f"Error fetching Res8 notices: {e}")
        return set()
    finally:
        conn.close()


def fetch_res35_notice_ids():
    """ntc_ids having at least one group in dbo.grp_res35 (Option 2), for the main table."""
    conn = get_sql_server_connection()
    if not conn:
        return set()
    try:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT DISTINCT g.ntc_id FROM dbo.grp_res35 r
            INNER JOIN dbo.grp g ON g.grp_id = r.grp_id
        """)
        return {str(r[0]) for r in cursor.fetchall()}
    except Exception as e:
        logger.error(f"Error fetching Res35 notices: {e}")
        return set()
    finally:
        conn.close()


def get_mdb_connection(retries=3, delay=2):
    """Connects to the local copy of the MDB database.

    Does NOT sync from the network here — syncing is handled out-of-band by the
    background refresher so that user requests never block on a slow/locked
    network share. Retries briefly on transient ODBC errors only."""
    conn_str = (
        f'DRIVER={{Microsoft Access Driver (*.mdb, *.accdb)}};'
        f'DBQ={LOCAL_MDB_PATH};'
        f'SystemDB={LOCAL_MDW_PATH};'
        f'UID={MDB_USERNAME};'
        f'PWD={MDB_PASSWORD};'
        f'ReadOnly=1;'
    )
    for attempt in range(1, retries + 1):
        try:
            conn = pyodbc.connect(conn_str)
            logger.info(f"Successfully connected to MDB database: {LOCAL_MDB_PATH}")
            return conn
        except pyodbc.Error as e:
            logger.warning(f"MDB connection attempt {attempt}/{retries} failed: {e}")
            if attempt < retries:
                logger.info(f"Retrying in {delay}s...")
                time.sleep(delay)
    logger.error(f"Error connecting to MDB database after {retries} attempts.")
    return None


def get_sql_server_connection():
    """Establishes a connection to the SQL Server database."""
    try:
        conn = pyodbc.connect(SQL_SERVER_CONN_STRING)
        logger.info("Successfully connected to the SQL Server database.")
        return conn
    except Exception as e:
        logger.error(f"Error connecting to SQL Server database: {e}")
        return None


def json_serial(obj):
    """JSON serializer for objects not serializable by default json code."""
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    raise TypeError(f"Type {type(obj)} not serializable")


def is_empty_sntrack_id(value):
    """Check if sntrack_id is empty or invalid."""
    if value is None:
        return True
    if isinstance(value, str):
        return value.strip() == ""
    if isinstance(value, (int, float)):
        return value == 0
    return False


def convert_to_serializable(row_dict):
    """Convert all values in a dict to JSON-serializable types."""
    result = {}
    for key, value in row_dict.items():
        if isinstance(value, (datetime, date)):
            result[key] = value.isoformat()
        else:
            result[key] = value
    return result


def fetch_data():
    """
    Fetch and merge data from MS Access (sntrack) and SQL Server (res908).
    Returns a list of dictionaries ready for JSON serialization.
    """
    logger.info("Starting data fetch...")

    # Fetch from MS Access (sntrack)
    access_conn = None
    sql_conn = None
    status_conn = None

    try:
        access_conn = get_mdb_connection()
        if not access_conn:
            return None, "Failed to connect to MS Access database"

        access_cursor = access_conn.cursor()
        access_query = """
                       SELECT [2d_date], BRREG, ADM, ntc_id, d_val_in, d_check_in, d_spr_out, d_complete, PUB, d_wmeeting, PUB2, PUB3, remarks, tex_remarks, CIRC, CIRC2, CIRC3, PHASE, tgt_ntc_id, subtoc, SUP, f_11_41, f_11_32A
                       FROM tblSpaceStnNotif
                       WHERE ntc_id <> 0 \
                       """
        access_cursor.execute(access_query)
        columns = [column[0] for column in access_cursor.description]

        access_data = []
        access_ids_seen = set()

        for row in access_cursor.fetchall():
            row_dict = dict(zip(columns, row))
            if row_dict.get('ntc_id'):
                val_id = str(row_dict['ntc_id'])
                row_dict['ntc_id'] = val_id
                access_ids_seen.add(val_id)
            access_data.append(row_dict)

        logger.info(f"Fetched {len(access_data)} records from MS Access")
    except Exception as e:
        logger.error(f"Error fetching from MS Access: {e}")
        return None, f"Error fetching from MS Access: {e}"
    finally:
        if access_conn:
            access_conn.close()

    # Fetch from SQL Server (res908)
    sql_lookup = {}

    sql_fields_lower = [
        'SatName', 'long_nom', 'ntwk_org', 'act_code', 'DateOfReceive',
        'TypeOfSubmission', 'DocumentumReference', 'InternalReference', 'sntrack_id',
        'SubmissionId'
    ]

    try:
        sql_conn = get_sql_server_connection()
        if sql_conn:
            sql_cursor = sql_conn.cursor()
            sql_query = """
                        SELECT SnsNtcId, \
                               SatName, \
                               long_nom, \
                               ntwk_org, \
                               DateOfReceive,
                               TypeOfSubmission, \
                               DocumentumReference, \
                               sntrack_id,
                               act_code, \
                               InternalReference, \
                               SubmissionId
                        FROM res908.Res908
                        WHERE SnsNtcId <> 0 \
                        """
            sql_cursor.execute(sql_query)
            sql_columns = [column[0] for column in sql_cursor.description]

            sql_lookup = {}  # key → last row (used for Access merge)
            sql_all_rows = []  # ALL rows preserved (used for SQL-only loop)

            for row in sql_cursor.fetchall():
                row_dict = dict(zip(sql_columns, row))
                key = str(row_dict['SnsNtcId'])
                sql_lookup[key] = row_dict
                sql_all_rows.append(row_dict)

            logger.info(f"Fetched {len(sql_all_rows)} records from SQL Server")
        else:
            logger.warning("Skipped SQL Server res908 (connection failed)")
    except Exception as e:
        logger.error(f"Error fetching from SQL Server: {e}")
    finally:
        if sql_conn:
            sql_conn.close()

    # Fetch status from dbo.Submissions table via SubmissionId
    status_lookup = {}
    try:
        status_conn = get_sql_server_connection()
        if status_conn:
            status_cursor = status_conn.cursor()
            status_query = """
                           SELECT s.SubmissionId, ss.st_desc
                           FROM dbo.Submissions s
                                    LEFT JOIN dbo.SubmissionsStatus ss ON s.Status = ss.st_cur
                           WHERE s.SubmissionId IS NOT NULL \
                           """
            status_cursor.execute(status_query)

            for row in status_cursor.fetchall():
                submission_id = str(row[0]) if row[0] else ''
                st_desc = row[1] if row[1] else ''
                if submission_id:
                    status_lookup[submission_id] = st_desc

            logger.info(f"Fetched {len(status_lookup)} status records from Submissions")
        else:
            logger.warning("Skipped status lookup (connection failed)")
    except Exception as e:
        logger.error(f"Error fetching status: {e}")
    finally:
        if status_conn:
            status_conn.close()

    # Merge data (Full Outer Join logic)
    logger.info("Merging data...")
    final_data = []

    access_columns_list = [
        '2d_date', 'BRREG', 'ADM', 'ntc_id', 'd_val_in', 'd_check_in',
        'd_spr_out', 'd_complete', 'PUB', 'd_wmeeting', 'PUB2', 'PUB3',
        'remarks', 'tex_remarks', 'CIRC', 'CIRC2', 'CIRC3', 'PHASE',
        'tgt_ntc_id', 'subtoc', 'SUP', 'f_11_41', 'f_11_32A'
    ]

    # Process records from Access
    for row in access_data:
        join_key = row.get('ntc_id')
        extra_info = sql_lookup.get(join_key, {})

        tgt_ntc_id = row.get('tgt_ntc_id')
        if tgt_ntc_id in (0, '', None):
            row['tgt_ntc_id'] = ''

        merged_row = {**row, **extra_info}
        merged_row['Source'] = 'Both' if extra_info else 'SNTrack'

        for field in sql_fields_lower:
            if field not in merged_row:
                merged_row[field] = ""

        # Add Status from Submissions table via SubmissionId
        submission_id = str(merged_row.get('SubmissionId', '')) if merged_row.get('SubmissionId') else ''
        merged_row['Status'] = status_lookup.get(submission_id, '')

        # Records from MS Access (sntrack) are always kept, even when they have
        # no matching e-submission record (no sntrack_id / no Status), so that
        # sntrack-only records remain visible in the tracking system.
        # sntrack-only records have no e-submission TypeOfSubmission; the source
        # table (tblSpaceStnNotif) tracks space-station notices, so label them
        # accordingly for the frontend TypeOfSubmission filter.
        if not merged_row.get('TypeOfSubmission'):
            merged_row['TypeOfSubmission'] = 'NotificationOfSpaceStation'

        final_data.append(convert_to_serializable(merged_row))

    # Add records only in SQL Server (not in Access).
    # Iterate over ALL SQL rows (not deduplicated dict) so that records
    # sharing the same SnsNtcId (e.g. placeholder value 1) are all preserved.
    for sql_row in sql_all_rows:
        sql_id = str(sql_row.get('SnsNtcId', ''))
        if sql_id in access_ids_seen:
            continue
        new_row = sql_row.copy()
        new_row['ntc_id'] = sql_id
        new_row['Source'] = 'E-submission'

        # This system tracks space-station notices only. Drop earth-station
        # submissions (e-submission records not present in sntrack).
        if str(new_row.get('TypeOfSubmission')) == 'NotificationOfEarthStation':
            continue

        if 'tgt_ntc_id' in new_row:
            tgt_ntc_id = new_row['tgt_ntc_id']
            if tgt_ntc_id in (0, '', None):
                new_row['tgt_ntc_id'] = ''
        else:
            new_row['tgt_ntc_id'] = ''

        for col in access_columns_list:
            if col not in new_row:
                new_row[col] = ""

        # Add Status from Submissions table via SubmissionId
        submission_id = str(new_row.get('SubmissionId', '')) if new_row.get('SubmissionId') else ''
        new_row['Status'] = status_lookup.get(submission_id, '')

        # Keep if has sntrack_id OR a non-empty Status
        if is_empty_sntrack_id(new_row.get('sntrack_id')) and not new_row.get('Status'):
            continue

        final_data.append(convert_to_serializable(new_row))

    # --- Merge ESIM Resolution classification ---
    esim_resolutions = fetch_esim_resolutions()
    esim_matched = 0
    res156_count = res169_count = res123_count = 0
    for row in final_data:
        ntc_id = str(row.get('ntc_id', ''))
        res_info = esim_resolutions.get(ntc_id, {})
        row['RES156'] = res_info.get('RES156', False)
        row['RES169'] = res_info.get('RES169', False)
        row['RES123'] = res_info.get('RES123', False)
        if res_info:
            esim_matched += 1
        if row['RES156']:
            res156_count += 1
        if row['RES169']:
            res169_count += 1
        if row['RES123']:
            res123_count += 1

    logger.info(f"ESIM merge: {esim_matched}/{len(final_data)} records have ESIM groups in SNS"
                f" (RES156={res156_count} RES169={res169_count} RES123={res123_count})")

    # --- Res35 (Option 2: notice has a group in dbo.grp_res35) ---
    res35_ids = fetch_res35_notice_ids()
    for row in final_data:
        row['RES35'] = str(row.get('ntc_id', '')) in res35_ids
    logger.info(f"Res35 merge: {sum(1 for r in final_data if r['RES35'])} records"
                f" ({len(res35_ids)} Res35 notices in SNS)")
    # --- Res8 (Res 8 submission, or referenced by one) ---
    res8_ids = fetch_res8_notice_ids()
    for row in final_data:
        row['RES8'] = str(row.get('ntc_id', '')) in res8_ids
    logger.info(f"Res8 merge: {sum(1 for r in final_data if r['RES8'])} records ({len(res8_ids)} notices in SNS)")
    return final_data, None


# ============================================================================
# In-memory cache + background refresher
# ----------------------------------------------------------------------------
# User requests must never block on a slow database or a locked network share.
# Strategy:
#   * On startup, load the latest cached_data.json into memory instantly.
#   * /api/data ALWAYS returns whatever is in the in-memory cache, instantly.
#   * A background thread refreshes when /api/refresh is called (on page open
#     and on the Refresh button), otherwise once every _REFRESH_INTERVAL
#     seconds (default 1 day). The refresh runs off the
#     request path, so the caller is never blocked.
# ============================================================================

# Seconds between automatic background refreshes when nobody asks for one
# (default: 1 day).
_REFRESH_INTERVAL = int(os.environ.get('REFRESH_INTERVAL_SECONDS', '86400'))

_cache_lock = threading.Lock()
_cache = {
    'data': None,  # list[dict] | None  (None until first successful load)
    'generated_at': None,  # ISO timestamp of last successful load
    'error': None,  # last error string, if the most recent attempt failed
}
# Signals the background thread to refresh immediately instead of waiting.
_refresh_now = threading.Event()


def _load_cache_from_disk():
    """Try to load cached_data.json from disk into the in-memory cache.
    Called at startup so /api/data can serve instantly without a DB fetch."""
    cache_path = os.path.join(FRONTEND_DIR, 'data', 'cached_data.json')
    try:
        with open(cache_path, 'r', encoding='utf-8') as _cf:
            cached = json.load(_cf)
        if cached.get('data') and cached.get('generated_at'):
            with _cache_lock:
                _cache['data'] = cached['data']
                _cache['generated_at'] = cached['generated_at']
                _cache['error'] = None
            logger.info(
                f"Loaded {len(cached['data'])} records from disk cache (generated {cached['generated_at'][:19]})")
            return True
    except Exception as e:
        logger.warning(f"Could not load disk cache: {e}")
    return False


def _timestamp_age_seconds(iso_str):
    """Return how many seconds ago the given ISO timestamp was.
    Returns None if the timestamp is missing or cannot be parsed."""
    if not iso_str:
        return None
    try:
        ts = datetime.fromisoformat(str(iso_str).replace('Z', '+00:00'))
    except (ValueError, AttributeError):
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - ts).total_seconds()


_IIS_CACHE_PATH = os.environ.get(
    'IIS_CACHE_PATH', r'\\intweb.itu.int\intwebroot\ITU-R\space\css\notification\data\cached_data.json').strip()


def _write_cache_files(cache_payload):
    """Persist the cache to disk locations used as an offline fallback by the
    Flask-served and IIS-served frontends. Best-effort; never raises."""
    cache_targets = [
        # 1. Local path – served by Flask at /data/cached_data.json
        os.path.join(FRONTEND_DIR, 'data', 'cached_data.json'),
    ]
    # 2. IIS file-share path – served as a static file by IIS. Set IIS_CACHE_PATH
    #    to an empty value on development machines so they never overwrite the
    #    production cache.
    if _IIS_CACHE_PATH:
        cache_targets.append(_IIS_CACHE_PATH)
    for cache_path in cache_targets:
        try:
            os.makedirs(os.path.dirname(cache_path), exist_ok=True)
            # Write to a temp file, then swap it in, so a browser reading the
            # file during a refresh never gets a half-written (invalid) JSON.
            tmp_path = cache_path + '.tmp'
            with open(tmp_path, 'w', encoding='utf-8') as _cf:
                json.dump(cache_payload, _cf, ensure_ascii=False)
            os.replace(tmp_path, cache_path)
            logger.info(f"Cache saved to {cache_path}")
        except Exception as _ce:
            logger.warning(f"Could not save cache to {cache_path}: {_ce}")

    # The HTML frontend reads a plain JSON array from ./data/data.json.
    # Write a second copy with just the array so the old filename stays in sync.
    try:
        frontend_data_path = os.path.join(FRONTEND_DIR, 'data', 'data.json')
        os.makedirs(os.path.dirname(frontend_data_path), exist_ok=True)
        with open(frontend_data_path, 'w', encoding='utf-8') as _cf:
            json.dump(cache_payload.get('data', []), _cf, ensure_ascii=False)
        logger.info(f"Frontend data.json synced: {frontend_data_path}")
    except Exception as _ce:
        logger.warning(f"Could not write data.json: {_ce}")


def _refresh_cache_once():
    """Sync network files, re-query the databases, and update the in-memory
    cache + on-disk fallback. Runs ONLY on the background thread."""
    logger.info("Background refresh: syncing network files...")
    _sync_local_files()
    data, error = fetch_data()
    now_iso = datetime.now(timezone.utc).isoformat()
    if error:
        # Keep serving the previous good data; just record the error.
        with _cache_lock:
            _cache['error'] = error
        logger.warning(f"Background refresh failed, keeping previous cache: {error}")
        return
    with _cache_lock:
        _cache['data'] = data
        _cache['generated_at'] = now_iso
        _cache['error'] = None
    _write_cache_files({'generated_at': now_iso, 'data': data})
    logger.info(f"Background refresh complete: {len(data)} records at {now_iso}")


def _background_refresher():
    """Daemon loop: refreshes every _REFRESH_INTERVAL seconds (default 1 day)
    and also whenever /api/refresh is called. On startup, the module-level code
    sets _refresh_now if the disk cache is older than 5 minutes, which triggers
    an immediate refresh on the first loop iteration."""
    while True:
        # Wait for the periodic timer OR an explicit refresh request, whichever
        # comes first. A pre-set _refresh_now (stale cache at startup) makes
        # this return immediately.
        _refresh_now.wait(timeout=_REFRESH_INTERVAL)
        _refresh_now.clear()
        try:
            _refresh_cache_once()
        except Exception as e:
            logger.error(f"Unexpected error in background refresher: {e}")


@app.route('/api/data', methods=['GET'])
@rate_limit
def get_data():
    """
    API endpoint to fetch all tracking data.
    Returns the in-memory cache instantly (stale-while-revalidate); never
    queries the database inline, so it cannot hang on a slow DB/network share.
    """
    with _cache_lock:
        data = _cache['data']
        generated_at = _cache['generated_at']
        error = _cache['error']

    if data is None:
        # Cache not warmed yet (very first request after startup).
        return jsonify({
            'success': False,
            'error': error or 'Data is still loading, please retry in a moment.',
            'data': []
        }), 503

    return jsonify({
        'success': True,
        'count': len(data),
        'timestamp': generated_at,
        'stale': error is not None,  # True => last refresh failed; data may be old
        'data': data
    })


@app.route('/api/refresh', methods=['GET', 'POST'])
@rate_limit
def refresh_data():
    """Ask the background refresher to reload ASAP. Returns immediately —
    the refresh happens off the request path, so this never blocks."""
    _refresh_now.set()
    with _cache_lock:
        generated_at = _cache['generated_at']
    return jsonify({
        'success': True,
        'message': 'Refresh scheduled.',
        'current_timestamp': generated_at
    }), 202


@app.route('/api/details/<ntc_id>', methods=['GET'])
@rate_limit
def get_notice_details(ntc_id):
    @app.route('/api/details/<ntc_id>', methods=['GET'])
    @rate_limit
    def get_notice_details(ntc_id):
        """Coordination statistics and Resolutions status (Res35, ESIM, Res8) for one notice (used by the row-click popup),
        read live from the SNS database (SQL Server, dbo). The parts are queried
        independently: if one fails the others are still returned."""
        if not ntc_id.isdigit():
            return jsonify({'success': False, 'error': 'ntc_id must be numeric'}), 400
        conn = get_sql_server_connection()
        if not conn:
            return jsonify({'success': False, 'error': 'SNS database not reachable'}), 503
        details, res35, esim, res8, errors = None, None, None, None, []
        try:
            try:
                details = fetch_notice_details(int(ntc_id), conn)
            except Exception as e:
                logger.error(f"Error fetching coordination details for ntc_id={ntc_id}: {e}")
                errors.append('Failed to query coordination data')
            try:
                res35 = fetch_res35(int(ntc_id), conn)
            except Exception as e:
                logger.error(f"Error fetching Res35 for ntc_id={ntc_id}: {e}")
                errors.append('Failed to query Res35 data')
            try:
                esim = fetch_esim_groups(int(ntc_id), conn)
            except Exception as e:
                logger.error(f"Error fetching ESIM groups for ntc_id={ntc_id}: {e}")
                errors.append('Failed to query ESIM data')
            try:
                res8 = fetch_res8(int(ntc_id), conn)
            except Exception as e:
                logger.error(f"Error fetching Res8 for ntc_id={ntc_id}: {e}")
                errors.append('Failed to query Res8 data')
        finally:
            conn.close()
        if details is None and res35 is None and esim is None and res8 is None:
            return jsonify({'success': False, 'error': '; '.join(errors)}), 500
        return jsonify({'success': True, 'ntc_id': ntc_id, 'details': details, 'res35': res35,
                        'esim': esim, 'res8': res8, 'errors': errors})
    read live from the SNS database (SQL Server, dbo). The two parts are queried
    independently: if one fails the other is still returned."""
    if not ntc_id.isdigit():
        return jsonify({'success': False, 'error': 'ntc_id must be numeric'}), 400
    conn = get_sql_server_connection()
    if not conn:
        return jsonify({'success': False, 'error': 'SNS database not reachable'}), 503
    details, res35, errors = None, None, []
    try:
        try:
            details = fetch_notice_details(int(ntc_id), conn)
        except Exception as e:
            logger.error(f"Error fetching coordination details for ntc_id={ntc_id}: {e}")
            errors.append('Failed to query coordination data')
        try:
            res35 = fetch_res35(int(ntc_id), conn)
        except Exception as e:
            logger.error(f"Error fetching Res35 for ntc_id={ntc_id}: {e}")
            errors.append('Failed to query Res35 data')
    esim = None
            try:
                esim = fetch_esim_groups(int(ntc_id), conn)
            except Exception as e:
                logger.error(f"Error fetching ESIM groups for ntc_id={ntc_id}: {e}")
                errors.append('Failed to query ESIM data')
            res8 = None
            try:
                res8 = fetch_res8(int(ntc_id), conn)
            except Exception as e:
                logger.error(f"Error fetching Res8 for ntc_id={ntc_id}: {e}")
                errors.append('Failed to query Res8 data')
        finally:
            conn.close()
        if details is None and res35 is None and esim is None and res8 is None:
            return jsonify({'success': False, 'error': '; '.join(errors)}), 500
        return jsonify({'success': True, 'ntc_id': ntc_id, 'details': details, 'res35': res35,
                        'esim': esim, 'res8': res8, 'errors': errors})


@app.route('/api/health', methods=['GET'])
def health_check():
    """Health check endpoint."""
    return jsonify({
        'status': 'healthy',
        'timestamp': datetime.now(timezone.utc).isoformat()
    })


@app.route('/')
@app.route('/index_api.html')
def serve_main():
    """Serve the main frontend page."""
    return send_from_directory(FRONTEND_DIR, 'index_api.html')


@app.route('/<path:filename>')
def serve_frontend(filename):
    """Serve frontend static files (HTML, data, etc.)."""
    return send_from_directory(FRONTEND_DIR, filename)


# 1. Load the latest cached data from disk so /api/data responds instantly.
# 2. Start the background refresher daemon.
# 3. Startup freshness check: if the cached data is older than 5 minutes,
#    trigger an immediate background refresh; otherwise keep serving the cache.
_loaded = _load_cache_from_disk()
if not _loaded:
    logger.warning("No disk cache found — /api/data will return 503 until "
                   "/api/refresh is called to perform the first data fetch.")

_refresher_thread = threading.Thread(
    target=_background_refresher, name='cache-refresher', daemon=True
)
_refresher_thread.start()

# Startup freshness window (5 minutes). If the cached data is older than this,
# schedule an immediate refresh so users don't see stale data.
_STARTUP_FRESH_SECONDS = 300
with _cache_lock:
    _startup_generated_at = _cache['generated_at']
_startup_age = _timestamp_age_seconds(_startup_generated_at)
if not _loaded or _startup_age is None or _startup_age > _STARTUP_FRESH_SECONDS:
    logger.info(
        f"Disk cache is stale (age={_startup_age if _startup_age is not None else 'N/A'}s) "
        "— scheduling immediate startup refresh."
    )
    _refresh_now.set()
else:
    logger.info(
        f"Disk cache is fresh (age={_startup_age:.0f}s) — skipping startup refresh."
    )

if __name__ == '__main__':
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
    cert_file = os.path.join(BASE_DIR, 'cert.pem')
    key_file = os.path.join(BASE_DIR, 'key.pem')
    ssl_ctx = (cert_file, key_file) if os.path.exists(cert_file) and os.path.exists(key_file) else None
    app.run(host='0.0.0.0', port=5001, debug=False, threaded=True, ssl_context=ssl_ctx)
