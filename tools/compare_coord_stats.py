"""
Compare the SRS detail-popup statistics with Wu's reference workbook
(coord_st_statistics_*.xlsx, columns J-Q).

READ-ONLY: only SELECT statements are run, through the same
fetch_notice_details() used by /api/details/<ntc_id>.

Usage (32-bit env, needed for the Access ODBC driver):
    tracking_env_32\\python.exe tools\\compare_coord_stats.py <workbook.xlsx> [out.csv] [--source-mdb]

    --source-mdb  read each notice's submitted MDB (column mdb_file) instead of
                  srs_all.mdb; this is the workbook's own data source, so it
                  checks the counting logic independently of BR processing.

Note: the workbook was computed from each administration's submitted MDB
(column mdb_file), while we query srs_all.mdb, so some differences are
expected to come from the data source rather than the code.
"""
import ast
import csv
import json
import os
import sys
import types
import zipfile
import xml.etree.ElementTree as ET

import pyodbc

_API_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'backend', 'api.py')
_SRS_FUNCS = {'_connect_srs', '_clean', '_summarize_coordination', 'fetch_notice_details'}


def load_srs_functions():
    """Load only the SRS query functions from backend/api.py.
    Importing api.py itself would start the cache refresher and touch the
    other databases, so the function definitions are extracted instead."""
    with open(_API_PATH, encoding='utf-8') as f:
        tree = ast.parse(f.read())
    body = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in _SRS_FUNCS]
    mod = types.ModuleType('api_srs')
    mod.pyodbc = pyodbc
    mod._SRS_MDB_PATH = os.environ.get('SRS_MDB_PATH', r'\\blue\dfs\br\BR_DATA\SPACE\SRS_DB\srs_all.mdb')
    exec(compile(ast.Module(body=body, type_ignores=[]), _API_PATH, 'exec'), mod.__dict__)
    return mod


api = load_srs_functions()

_NS = {'m': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}


def read_xlsx(path):
    """Minimal stdlib reader for the first sheet: returns a list of dicts."""
    with zipfile.ZipFile(path) as z:
        shared = []
        if 'xl/sharedStrings.xml' in z.namelist():
            for si in ET.fromstring(z.read('xl/sharedStrings.xml')).findall('m:si', _NS):
                shared.append(''.join(t.text or '' for t in si.iter('{%s}t' % _NS['m'])))
        sheet = ET.fromstring(z.read('xl/worksheets/sheet1.xml'))
    rows = []
    for r in sheet.find('m:sheetData', _NS).findall('m:row', _NS):
        cells = {}
        for c in r.findall('m:c', _NS):
            col = ''.join(ch for ch in c.get('r') if ch.isalpha())
            v = c.find('m:v', _NS)
            if c.get('t') == 'inlineStr':
                val = ''.join(t.text or '' for t in c.iter('{%s}t' % _NS['m']))
            elif v is None:
                val = ''
            elif c.get('t') == 's':
                val = shared[int(v.text)]
            else:
                val = v.text
            cells[col] = val
        rows.append(cells)
    header = rows[0]
    return [{header[k]: row.get(k, '') for k in header} for row in rows[1:] if row.get('A')]


def as_set(text):
    text = (text or '').strip()
    return set(json.loads(text)) if text else set()


def as_dict_of_sets(text):
    text = (text or '').strip()
    return {k: set(v) for k, v in json.loads(text).items()} if text else {}


def as_int(text):
    text = (text or '').strip()
    return int(float(text)) if text else 0


def fmt(v):
    if isinstance(v, set):
        return ','.join(sorted(v))
    if isinstance(v, dict):
        return json.dumps({k: sorted(s) for k, s in sorted(v.items())})
    return str(v)


def compare_row(row, d):
    """Yield (column, expected, actual) for every mismatch."""
    provn, tr = d['provn'], d['tr_aff_ntw']
    o_details = {k: set(v) for k, v in provn['coord_st_details'].get('O', {}).items()}
    checks = [
        ('J agree_st_o_count', as_int(row['agree_st_o_count']), len(provn['agree_st'].get('O', []))),
        ('K coord_st_o_count', as_int(row['coord_st_o_count']), len(provn['coord_st'].get('O', []))),
        ('L agree_st_o_adm_list', as_set(row['agree_st_o_adm_list']), set(provn['agree_st'].get('O', []))),
        ('M coord_prov_values', as_set(row['coord_prov_values']), set(provn['coord_prov_values'])),
        ('N coord_st_o_details', as_dict_of_sets(row['coord_st_o_details']), o_details),
        ('O coord_st_o_adm_list', as_set(row['coord_st_o_adm_list']), set(provn['coord_st'].get('O', []))),
        ('P info in tr_aff_ntw_data', row['info in tr_aff_ntw_data'].strip() == 'Yes', tr['has_data']),
        ('Q tr_aff_ntw_coord_prov', as_set(row['tr_aff_ntw_coord_prov']), set(tr['coord_prov_values'])),
    ]
    for name, expected, actual in checks:
        if expected != actual:
            yield name, expected, actual


def main():
    args = [a for a in sys.argv[1:] if a != '--source-mdb']
    use_submitted_mdb = '--source-mdb' in sys.argv
    if not args:
        sys.exit(__doc__)
    out_path = args[1] if len(args) > 1 else 'compare_coord_stats_result.csv'
    rows = read_xlsx(args[0])
    srs_path = api._SRS_MDB_PATH

    not_in_srs, all_match, mismatches = [], 0, []
    per_column = {}
    for row in rows:
        ntc_id = row['ntc_id'].strip()
        # --source-mdb: run the same code against the submitted MDB the
        # workbook was built from, to check the counting logic itself.
        api._SRS_MDB_PATH = row.get('mdb_file', '').strip() if use_submitted_mdb else srs_path
        try:
            d = api.fetch_notice_details(int(ntc_id))
        except Exception as e:
            print(f'{ntc_id}: cannot read {api._SRS_MDB_PATH}: {str(e)[:100]}')
            not_in_srs.append(ntc_id)
            continue
        if not d['in_srs']:
            not_in_srs.append(ntc_id)
            continue
        diffs = list(compare_row(row, d))
        if not diffs:
            all_match += 1
        for name, expected, actual in diffs:
            per_column[name] = per_column.get(name, 0) + 1
            mismatches.append([ntc_id, row.get('SatName', ''), row.get('TypeOfSubmission', ''),
                               name, fmt(expected), fmt(actual), row.get('mdb_file', '')])

    with open(out_path, 'w', newline='', encoding='utf-8-sig') as f:
        w = csv.writer(f)
        w.writerow(['ntc_id', 'SatName', 'TypeOfSubmission', 'column', 'excel (expected)',
                    'submitted MDB (actual)' if use_submitted_mdb else 'srs_all.mdb (actual)', 'mdb_file'])
        w.writerows(mismatches)

    checked = len(rows) - len(not_in_srs)
    print(f'Workbook rows:          {len(rows)}')
    print(f'Source:                 {"submitted MDB (mdb_file column)" if use_submitted_mdb else srs_path}')
    print(f'Notice not in source:   {len(not_in_srs)} (skipped)')
    print(f'Compared:               {checked}')
    print(f'  all 8 columns match:  {all_match}')
    print(f'  with differences:     {checked - all_match}')
    for name in sorted(per_column):
        print(f'    {name:28s} {per_column[name]} mismatches')
    print(f'Mismatch list written to {out_path}')


if __name__ == '__main__':
    main()
