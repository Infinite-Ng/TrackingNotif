import pyodbc
import json
import os
from datetime import date, datetime
from logging_config import logger

# --- Configuration ---
# Paths to your source MS Access database files.
TRACKING_MDB_PATH = r'M:\BR_DATA\SPACE\SNTRACK\sntrdat.mdb'
mdw_file = r'M:\BR_DATA\SPACE\SNTRACK\sntrapp.mdw'
# RES908_MDB_PATH = r"W:\codingSpace\ITU\ITU_TrackingNotif\dataset\res908.mdb"

OUTPUT_FILE = os.path.join(os.path.dirname(__file__), '../frontend/data/data.json')

SQL_SERVER_CONN_STRING = (
    "DRIVER={SQL Server};"
    "SERVER=sydney.itu.int;"
    "DATABASE=SpaceNetworkSystem;"
    "UID=sns_a;"
    "PWD=sns_a_5678;"
)

def get_mdb_connection(path, mdw_path, username, password):
    """Establishes a connection to an MS Access database."""
    try:
        conn_str = f'DRIVER={{Microsoft Access Driver (*.mdb, *.accdb)}};DBQ={path};SystemDB={mdw_path};UID={username};PWD={password};'
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




def fetch_and_export():
    print("Getting the live data from database...")

    access_conn = get_mdb_connection(TRACKING_MDB_PATH, mdw_file, 'spruser01', 'spruser01')
    if not access_conn:
        return

    access_cursor = access_conn.cursor()
    access_query = """
            SELECT [2d_date], BRREG, ADM, ntc_id, d_val_in, d_check_in, d_spr_out, d_complete, PUB, d_wmeeting, PUB2, PUB3, remarks, tex_remarks, CIRC, CIRC2, CIRC3, PHASE, tgt_ntc_id, subtoc
            FROM tblSpaceStnNotif
            WHERE ntc_id <> 0
            """
    access_cursor.execute(access_query)

    columns = [column[0] for column in access_cursor.description]

    access_data = []
    for row in access_cursor.fetchall():
        row_dict = dict(zip(columns, row))
        if row_dict.get('ntc_id'):
            row_dict['ntc_id'] = str(row_dict['ntc_id'])
        access_data.append(row_dict)

    access_conn.close()
    print(f"✅ finished: {len(access_data)} records")


    print("Reading SQL Server res908 ...")
    sql_conn = get_sql_server_connection()
    sql_lookup = {}

    sql_fields_lower = [
        'SatName', 'long_nom', 'ntwk_org', 'act_code', 'DateofReceive',
        'TypeOfSubmission', 'DocumentumReference', 'InternalReference', 'sntrack_id'
    ]

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

        sql_conn.close()
        print(f"✅ finished: {len(sql_lookup)} records")
    else:
        print("⚠️ skipped SQL Server res908 ...")

    print("Merging Data...")
    # final_data = []
    #
    # for row in access_data:
    #     current_id = row.get('ntc_id')
    #
    #     extra_info = sql_lookup.get(current_id, {})
    #
    #     # if key confliction, extra_info will cover access data
    #     merged_row = {**row, **extra_info}
    #
    #     for field in sql_columns:
    #         if field not in merged_row:
    #             merged_row[field] = ""
    #
    #     final_data.append(merged_row)

    print("🚀Merging (LEFT JOIN t2 ON t1.ntc_id = CAST(t2.sns_ntc_id))...")
    final_data = []

    for row in access_data:
        # Join Key
        join_key = row.get('ntc_id')

        # 在 SQL Server 字典中查找 (Lookup)
        # 如果找不到，返回空字典 {} (符合 Left Join 逻辑)
        extra_info = sql_lookup.get(join_key, {})

        # 合并数据
        merged_row = {**row, **extra_info}

        # 数据清洗：补全 SQL Server 缺失的字段为空字符串
        # 否则前端 DataTables 会报错 "Requested unknown parameter"
        for field in sql_fields_lower:
            if field not in merged_row:
                merged_row[field] = ""


        final_data.append(merged_row)


    print(f"Writing to {OUTPUT_FILE} ...")
    try:
        with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
            json.dump(final_data, f, ensure_ascii=False, default=json_serial)
        print("🎉 Succeeded, data.json is updated.")
    except Exception as e:
        print(f"❌ written failed: {e}")


if __name__ == '__main__':
    fetch_and_export()