#!/usr/bin/env python3
"""Minimal API backend for iSTAR_dashboard.html.

Endpoints:
  GET /health
  GET /api/dashboard-data
  GET /api/rdl-manifest

By default, /api/dashboard-data serves dashboard_data.json from disk.
For SQL-backed runtime mode, set:
  ISTAR_SQL_CONNECTION_STRING
  ISTAR_SQL_JSON_QUERY
where query returns one row/one column containing dashboard JSON.
"""

import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse
from xml.etree import ElementTree as ET

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RDL_DIR = os.path.join(BASE, 'Data', 'RDLs')
DATA_FILE = os.environ.get('ISTAR_DASHBOARD_DATA_FILE', os.path.join(BASE, 'dashboard_data.json'))
HOST = os.environ.get('ISTAR_BACKEND_HOST', '0.0.0.0')
PORT = int(os.environ.get('ISTAR_BACKEND_PORT', '8765'))


def _load_rdl_manifest():
    out = {}
    if not os.path.isdir(RDL_DIR):
        return out
    for fn in sorted(os.listdir(RDL_DIR)):
        if not fn.lower().endswith('.rdl'):
            continue
        path = os.path.join(RDL_DIR, fn)
        try:
            root = ET.parse(path).getroot()
        except Exception:
            continue
        datasets = []
        for ds in root.findall('.//{*}DataSet'):
            q = ds.find('./{*}Query')
            if q is None:
                continue
            cmd = (q.findtext('./{*}CommandText') or '').strip()
            qps = [p.get('Name', '').lstrip('@') for p in q.findall('./{*}QueryParameters/{*}QueryParameter')]
            datasets.append({'dataset': ds.get('Name', ''), 'queryParameters': qps, 'commandText': cmd})
        out[fn] = datasets
    return out


RDL_MANIFEST = _load_rdl_manifest()


def _json_from_sql():
    conn_str = os.environ.get('ISTAR_SQL_CONNECTION_STRING', '').strip()
    qry = os.environ.get('ISTAR_SQL_JSON_QUERY', '').strip()
    if not conn_str or not qry:
        return None
    try:
        import pyodbc  # optional
    except ImportError as exc:
        raise RuntimeError('pyodbc is required for SQL mode.') from exc
    cn = pyodbc.connect(conn_str, timeout=30)
    try:
        cur = cn.cursor()
        cur.execute(qry)
        row = cur.fetchone()
        if not row:
            raise RuntimeError('ISTAR_SQL_JSON_QUERY returned no rows.')
        payload = row[0]
        if payload is None:
            raise RuntimeError('ISTAR_SQL_JSON_QUERY returned NULL payload.')
        if isinstance(payload, (dict, list)):
            return payload
        return json.loads(str(payload))
    finally:
        cn.close()


def _json_from_file():
    with open(DATA_FILE, 'r', encoding='utf-8') as f:
        return json.load(f)


def get_dashboard_payload():
    payload = _json_from_sql()
    if payload is not None:
        return payload
    return _json_from_file()


class Handler(BaseHTTPRequestHandler):
    def _send_json(self, code, obj):
        data = json.dumps(obj).encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(data)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET,OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.end_headers()

    def do_GET(self):
        path = urlparse(self.path).path
        try:
            if path == '/health':
                self._send_json(200, {'ok': True})
                return
            if path == '/api/rdl-manifest':
                self._send_json(200, {'files': RDL_MANIFEST})
                return
            if path == '/api/dashboard-data':
                self._send_json(200, get_dashboard_payload())
                return
            self._send_json(404, {'error': 'not found'})
        except Exception as exc:
            self._send_json(500, {'error': str(exc)})


if __name__ == '__main__':
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f'iSTAR backend listening on http://{HOST}:{PORT}')
    print('Endpoints: /health, /api/dashboard-data, /api/rdl-manifest')
    server.serve_forever()
