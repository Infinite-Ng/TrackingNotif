"""
Flask API Backend for ITU Notice Tracking System
Provides real-time data from SQL Server and MS Access databases
"""
import json
import os
import shutil
import time
import pyodbc
from flask import Flask, jsonify, send_from_directory, request
from flask_cors import CORS
from datetime import date, datetime, timezone
import logging
from functools import wraps

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# --- Simple in-memory rate limiter ---
# Tracks request timestamps per client IP; allows max_requests per window_seconds.
_rate_limit_store = {}  # {ip: [timestamp, ...]}
_RATE_LIMIT_WINDOW = 60     # seconds
_RATE_LIMIT_MAX = 30        # max requests per window per IP


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
MDB_USERNAME = os.environ.get('MDB_USER', 'spruser01')
MDB_PASSWORD = os.environ.get('MDB_PASSWORD', 'spruser01')

# Local data directory – files here are used for all connections.
# Manually pre-seeded; auto-refreshed on every /api/data request.
LOCAL_DATA_DIR = r'C:\Users\xianwu\Desktop\codingSpace\TrackingNotif\data'
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


# Initial sync at startup
_sync_local_files()

SQL_SERVER_CONN_STRING = (
    "DRIVER={SQL Server};"
    f"SERVER={os.environ.get('SQL_SERVER_HOST', 'sydney.itu.int')};"
    f"DATABASE={os.environ.get('SQL_SERVER_DB', 'SpaceNetworkSystem')};"
    f"UID={os.environ.get('SQL_SERVER_USER', 'sns_a')};"
    f"PWD={os.environ.get('SQL_SERVER_PASSWORD', 'sns_a_5678')};"
)

def get_mdb_connection(retries=3, delay=5):
    """Connects to the local copy of the MDB database.
    Syncs local files from the network first; retries on transient errors."""
    # Refresh local copies before connecting
    _sync_local_files()
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
            SELECT [2d_date], BRREG, ADM, ntc_id, d_val_in, d_check_in, 
                   d_spr_out, d_complete, PUB, d_wmeeting, PUB2, PUB3, 
                   remarks, tex_remarks, CIRC, CIRC2, CIRC3, PHASE, 
                   tgt_ntc_id, subtoc, SUP, f_11_41, f_11_32A
            FROM tblSpaceStnNotif
            WHERE ntc_id <> 0
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
                SELECT SnsNtcId, SatName, long_nom, ntwk_org, DateOfReceive, 
                       TypeOfSubmission, DocumentumReference, sntrack_id, 
                       act_code, InternalReference, SubmissionId
                FROM res908.Res908
                WHERE SnsNtcId <> 0
            """
            sql_cursor.execute(sql_query)
            sql_columns = [column[0] for column in sql_cursor.description]

            for row in sql_cursor.fetchall():
                row_dict = dict(zip(sql_columns, row))
                key = str(row_dict['SnsNtcId'])
                sql_lookup[key] = row_dict

            logger.info(f"Fetched {len(sql_lookup)} records from SQL Server")
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
                WHERE s.SubmissionId IS NOT NULL
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

        for field in sql_fields_lower:
            if field not in merged_row:
                merged_row[field] = ""

        if is_empty_sntrack_id(merged_row.get('sntrack_id')):
            continue

        # Add Status from Submissions table via SubmissionId
        submission_id = str(merged_row.get('SubmissionId', '')) if merged_row.get('SubmissionId') else ''
        merged_row['Status'] = status_lookup.get(submission_id, '')

        final_data.append(convert_to_serializable(merged_row))

    # Add records only in SQL Server (not in Access)
    for sql_id, sql_row in sql_lookup.items():
        if sql_id not in access_ids_seen:
            new_row = sql_row.copy()
            new_row['ntc_id'] = sql_id

            if 'tgt_ntc_id' in new_row:
                tgt_ntc_id = new_row['tgt_ntc_id']
                if tgt_ntc_id in (0, '', None):
                    new_row['tgt_ntc_id'] = ''
            else:
                new_row['tgt_ntc_id'] = ''

            for col in access_columns_list:
                if col not in new_row:
                    new_row[col] = ""

            if is_empty_sntrack_id(new_row.get('sntrack_id')):
                continue

            # Add Status from Submissions table via SubmissionId
            submission_id = str(new_row.get('SubmissionId', '')) if new_row.get('SubmissionId') else ''
            new_row['Status'] = status_lookup.get(submission_id, '')

            final_data.append(convert_to_serializable(new_row))

    logger.info(f"Total merged records: {len(final_data)}")
    return final_data, None


@app.route('/api/data', methods=['GET'])
@rate_limit
def get_data():
    """
    API endpoint to fetch all tracking data.
    Returns JSON array of notice records.
    """
    try:
        data, error = fetch_data()
        if error:
            return jsonify({
                'success': False,
                'error': error,
                'data': []
            }), 500

        # Save a local cache for offline fallback
        now_iso = datetime.now(timezone.utc).isoformat()
        cache_payload = {'generated_at': now_iso, 'data': data}

        # Write to multiple locations so both Flask-served and IIS-served frontends
        # can fall back to cached data when the backend is offline.
        cache_targets = [
            # 1. Local path – served by Flask at /data/cached_data.json
            os.path.join(FRONTEND_DIR, 'data', 'cached_data.json'),
            # 2. IIS file-share path – served as a static file by IIS
            r'\\intweb.itu.int\intwebroot\ITU-R\space\css\notification\data\cached_data.json',
        ]
        for cache_path in cache_targets:
            try:
                os.makedirs(os.path.dirname(cache_path), exist_ok=True)
                with open(cache_path, 'w', encoding='utf-8') as _cf:
                    json.dump(cache_payload, _cf, ensure_ascii=False)
                logger.info(f"Cache saved to {cache_path}")
            except Exception as _ce:
                logger.warning(f"Could not save cache to {cache_path}: {_ce}")

        return jsonify({
            'success': True,
            'count': len(data),
            'timestamp': now_iso,
            'data': data
        })
    except Exception as e:
        logger.error(f"API error: {e}")
        return jsonify({
            'success': False,
            'error': str(e),
            'data': []
        }), 500


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


if __name__ == '__main__':
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
    cert_file = os.path.join(BASE_DIR, 'cert.pem')
    key_file  = os.path.join(BASE_DIR, 'key.pem')
    ssl_ctx = (cert_file, key_file) if os.path.exists(cert_file) and os.path.exists(key_file) else None
    app.run(host='0.0.0.0', port=5001, debug=False, threaded=True, ssl_context=ssl_ctx)
