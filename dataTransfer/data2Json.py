import pyodbc
import json
import os
from datetime import date, datetime
from logging_config import logger

# --- Configuration ---
# Paths to your source MS Access database files.
TRACKING_MDB_PATH = r'M:\BR_DATA\SPACE\SNTRACK\sntrdat.mdb'
mdw_file = r'M:\BR_DATA\SPACE\SNTRACK\sntrapp.mdw'

OUTPUT_FILE = os.path.join(os.path.dirname(__file__), '../frontend/data/data.json')

# --- Credentials: prefer environment variables, fall back to defaults ---
MDB_USER = os.environ.get('MDB_USER', 'spruser01')
MDB_PASSWORD = os.environ.get('MDB_PASSWORD', 'spruser01')
SQL_SERVER_USER = os.environ.get('SQL_SERVER_USER', 'sns_a')
SQL_SERVER_PASSWORD = os.environ.get('SQL_SERVER_PASSWORD', 'sns_a_5678')
SQL_SERVER_HOST = os.environ.get('SQL_SERVER_HOST', 'sydney.itu.int')
SQL_SERVER_DB = os.environ.get('SQL_SERVER_DB', 'SpaceNetworkSystem')

SQL_SERVER_CONN_STRING = (
    "DRIVER={SQL Server};"
    f"SERVER={SQL_SERVER_HOST};"
    f"DATABASE={SQL_SERVER_DB};"
    f"UID={SQL_SERVER_USER};"
    f"PWD={SQL_SERVER_PASSWORD};"
)

def get_mdb_connection(path, mdw_path, username, password):
    """Establishes a connection to an MS Access database."""
    try:
        # for driver in pyodbc.drivers():
        #     print(driver)
        # print(driver := pyodbc.drivers())
        conn_str = f'DRIVER={{Microsoft Access Driver (*.mdb, *.accdb)}};DBQ={path};SystemDB={mdw_path};UID={username};PWD={password};ReadOnly=1;'
        conn = pyodbc.connect(conn_str)
        logger.info(f"Successfully connected to MDB database: {path}")
        return conn
    except pyodbc.Error as e:
        logger.error(f"Error connecting to MDB database: {e}")
        return None

def get_sql_server_connection():
    """Establishes a connection to the read-only SQL Server database."""
    try:
        conn = pyodbc.connect(SQL_SERVER_CONN_STRING)
        # temp_cursor = conn.cursor()
        # temp_cursor.execute('SELECT TOP * FROM res908')
        # column_names = [column[0] for column in temp_cursor.description]

        # print(column_names)
        logger.info("Successfully connected to the SQL Server database.")
        return conn
    except Exception as e:
        logger.error(f"Error connecting to SQL Server database: {e}")
        return None


def json_serial(obj):
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    raise TypeError(f"Type {type(obj)} not serializable")


def is_empty_sntrack_id(value):
    if value is None:
        return True
    if isinstance(value, str):
        return value.strip() == ""
    if isinstance(value, (int, float)):
        return value == 0
    return False


def fetch_and_export():
    print("Getting the live data from database...")

    access_conn = get_mdb_connection(TRACKING_MDB_PATH, mdw_file, MDB_USER, MDB_PASSWORD)
    if not access_conn:
        return

    try:
        access_cursor = access_conn.cursor()
        access_query = """
                SELECT [2d_date], BRREG, ADM, ntc_id, d_val_in, d_check_in, d_spr_out, d_complete, PUB, d_wmeeting, PUB2, PUB3, remarks, tex_remarks, CIRC, CIRC2, CIRC3, PHASE, tgt_ntc_id, subtoc, SUP, f_11_41, f_11_32A
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

        print(f"finished: {len(access_data)} records")
    finally:
        access_conn.close()

    print("Reading SQL Server res908 ...")
    sql_conn = None
    sql_lookup = {}

    sql_fields_lower = [
        'SatName', 'long_nom', 'ntwk_org', 'act_code', 'DateOfReceive',
        'TypeOfSubmission', 'DocumentumReference', 'InternalReference', 'sntrack_id'
    ]

    try:
        sql_conn = get_sql_server_connection()
        if sql_conn:
            sql_cursor = sql_conn.cursor()
            sql_query = """
                    SELECT SnsNtcId, SatName, long_nom, ntwk_org, DateOfReceive, TypeOfSubmission, DocumentumReference, sntrack_id, act_code, InternalReference 
                    FROM res908.Res908
                    WHERE SnsNtcId <> 0
            """
            sql_cursor.execute(sql_query)

            sql_columns = [column[0] for column in sql_cursor.description]

            for row in sql_cursor.fetchall():
                row_dict = dict(zip(sql_columns, row))
                key = str(row_dict['SnsNtcId'])
                sql_lookup[key] = row_dict

            print(f"finished: {len(sql_lookup)} records")
        else:
            print("skipped SQL Server res908 ...")
    finally:
        if sql_conn:
            sql_conn.close()

    print("Merging Data (Full Outer Logic)...")
    final_data = []

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

        final_data.append(merged_row)

    access_columns_list = [
        '2d_date', 'BRREG', 'ADM', 'ntc_id', 'd_val_in', 'd_check_in',
        'd_spr_out', 'd_complete', 'PUB', 'd_wmeeting', 'PUB2', 'PUB3',
        'remarks', 'tex_remarks', 'CIRC', 'CIRC2', 'CIRC3', 'PHASE',
        'tgt_ntc_id', 'subtoc', 'SUP', 'f_11_41', 'f_11_32A'
    ]

    for sql_id, sql_row in sql_lookup.items():
        if sql_id not in access_ids_seen:
            # this ID not in Access (sntrack) ，is esubmission only
            new_row = sql_row.copy()

            # mapping
            new_row['ntc_id'] = sql_id  # 填补 Notice ID

            if 'tgt_ntc_id' in new_row:
                tgt_ntc_id = new_row['tgt_ntc_id']
                if tgt_ntc_id in (0, '', None):
                    new_row['tgt_ntc_id'] = ''
            else:
                new_row['tgt_ntc_id'] = ''

            # if 'DateOfReceive' in new_row:
            #     new_row['BRREG'] = new_row['DateOfReceive']

            for col in access_columns_list:
                if col not in new_row:
                    new_row[col] = ""

            if is_empty_sntrack_id(new_row.get('sntrack_id')):
                continue

            final_data.append(new_row)

    print(f"Writing to {OUTPUT_FILE} ...")
    try:
        with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
            json.dump(final_data, f, ensure_ascii=False, default=json_serial)
        print("Succeeded, data.json is updated.")
    except Exception as e:
        print(f"written failed: {e}")


if __name__ == '__main__':
    fetch_and_export()