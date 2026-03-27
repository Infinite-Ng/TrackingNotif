"""
Flask API Backend for ITU Notice Tracking System
Provides real-time data from SQL Server and MS Access databases
"""
import pyodbc
from flask import Flask, jsonify
from flask_cors import CORS
from datetime import date, datetime
import logging
import json
import os

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

app = Flask(__name__)
# Enable CORS for all routes with explicit configuration
CORS(app, resources={r"/*": {"origins": "*", "methods": ["GET", "POST", "OPTIONS"], "allow_headers": ["Content-Type", "Authorization"]}})

# --- Configuration ---
TRACKING_MDB_PATH = r'M:\BR_DATA\SPACE\SNTRACK\sntrdat.mdb'
MDW_FILE = r'M:\BR_DATA\SPACE\SNTRACK\sntrapp.mdw'
MDB_USERNAME = 'spruser01'
MDB_PASSWORD = 'spruser01'

# Backup JSON file paths
BACKUP_JSON_LOCAL = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'frontend', 'data', 'cached_data.json')
BACKUP_JSON_SERVER = r'\\intweb.itu.int\intwebroot\ITU-R\space\css\notification\data\cached_data.json'

SQL_SERVER_CONN_STRING = (
    "DRIVER={SQL Server};"
    "SERVER=sydney.itu.int;"
    "DATABASE=SpaceNetworkSystem;"
    "UID=sns_a;"
    "PWD=sns_a_5678;"
)

def get_mdb_connection():
    """Establishes a connection to an MS Access database."""
    try:
        conn_str = (
            f'DRIVER={{Microsoft Access Driver (*.mdb, *.accdb)}};'
            f'DBQ={TRACKING_MDB_PATH};'
            f'SystemDB={MDW_FILE};'
            f'UID={MDB_USERNAME};'
            f'PWD={MDB_PASSWORD};'
        )
        conn = pyodbc.connect(conn_str)
        logger.info(f"Successfully connected to MDB database: {TRACKING_MDB_PATH}")
        return conn
    except pyodbc.Error as e:
        logger.error(f"Error connecting to MDB database: {e}")
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
    access_conn = get_mdb_connection()
    if not access_conn:
        return None, "Failed to connect to MS Access database"

    try:
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

        access_conn.close()
        logger.info(f"Fetched {len(access_data)} records from MS Access")
    except Exception as e:
        logger.error(f"Error fetching from MS Access: {e}")
        if access_conn:
            access_conn.close()
        return None, f"Error fetching from MS Access: {e}"

    # Fetch from SQL Server (res908)
    sql_conn = get_sql_server_connection()
    sql_lookup = {}

    sql_fields_lower = [
        'SatName', 'long_nom', 'ntwk_org', 'act_code', 'DateOfReceive',
        'TypeOfSubmission', 'DocumentumReference', 'InternalReference', 'sntrack_id',
        'SubmissionId'
    ]

    if sql_conn:
        try:
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

            sql_conn.close()
            logger.info(f"Fetched {len(sql_lookup)} records from SQL Server")
        except Exception as e:
            logger.error(f"Error fetching from SQL Server: {e}")
            if sql_conn:
                sql_conn.close()
    else:
        logger.warning("Skipped SQL Server res908 (connection failed)")

    # Fetch status from dbo.Submissions table via SubmissionId
    status_lookup = {}
    status_conn = get_sql_server_connection()
    if status_conn:
        try:
            status_cursor = status_conn.cursor()
            # Query to get status for each SubmissionId
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
            
            status_conn.close()
            logger.info(f"Fetched {len(status_lookup)} status records from Submissions")
        except Exception as e:
            logger.error(f"Error fetching status: {e}")
            if status_conn:
                status_conn.close()
    else:
        logger.warning("Skipped status lookup (connection failed)")

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


def save_backup_json(data):
    """Save data to backup JSON files for offline access."""
    timestamp = datetime.now().isoformat()
    backup_content = {
        'success': True,
        'count': len(data),
        'timestamp': timestamp,
        'data': data
    }
    
    # Save to local frontend/data folder
    try:
        os.makedirs(os.path.dirname(BACKUP_JSON_LOCAL), exist_ok=True)
        with open(BACKUP_JSON_LOCAL, 'w', encoding='utf-8') as f:
            json.dump(backup_content, f, ensure_ascii=False)
        logger.info(f"Saved backup JSON to local: {BACKUP_JSON_LOCAL}")
    except Exception as e:
        logger.error(f"Failed to save local backup JSON: {e}")
    
    # Save to server location
    try:
        os.makedirs(os.path.dirname(BACKUP_JSON_SERVER), exist_ok=True)
        with open(BACKUP_JSON_SERVER, 'w', encoding='utf-8') as f:
            json.dump(backup_content, f, ensure_ascii=False)
        logger.info(f"Saved backup JSON to server: {BACKUP_JSON_SERVER}")
    except Exception as e:
        logger.error(f"Failed to save server backup JSON: {e}")


@app.route('/api/data', methods=['GET'])
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
        
        # Save to backup JSON files
        save_backup_json(data)
        
        return jsonify({
            'success': True,
            'count': len(data),
            'timestamp': datetime.now().isoformat(),
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
        'timestamp': datetime.now().isoformat()
    })


@app.route('/', methods=['GET'])
def index():
    """Root endpoint with API information."""
    return jsonify({
        'name': 'ITU Notice Tracking API',
        'version': '1.0.0',
        'endpoints': {
            '/api/data': 'GET - Fetch all tracking data',
            '/api/health': 'GET - Health check'
        }
    })


if __name__ == '__main__':
    import os
    
    cert_dir = os.path.dirname(os.path.abspath(__file__))
    cert_file = os.path.join(cert_dir, 'cert.pem')
    key_file = os.path.join(cert_dir, 'key.pem')
    
    # Check if SSL certificates exist
    if os.path.exists(cert_file) and os.path.exists(key_file):
        # Run with HTTPS
        logger.info("Starting server with HTTPS on port 5000...")
        app.run(host='0.0.0.0', port=5000, debug=True, ssl_context=(cert_file, key_file))
    else:
        # Run with HTTP (fallback)
        logger.info("SSL certificates not found. Starting server with HTTP on port 5000...")
        logger.info(f"To enable HTTPS, run: python generate_cert.py")
        app.run(host='0.0.0.0', port=5000, debug=True)