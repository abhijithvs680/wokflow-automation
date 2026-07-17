"""MySQL access (read-only): spreadsheet index from viz_livespace_files.

Replicates Sys\\Livespaces\\Liveapps\\Selector::getLivespaceSpreadsheetList and
the workflow builder's Customblockpopup query.
"""
from __future__ import annotations

import pymysql

from ..config import get_settings


def _conn():
    s = get_settings()
    return pymysql.connect(
        host=s.mysql_host,
        port=s.mysql_port,
        user=s.mysql_user,
        password=s.mysql_password,
        database=s.mysql_db,
        cursorclass=pymysql.cursors.DictCursor,
        connect_timeout=10,
    )


SS_INDEX_SQL = """
SELECT f.lid, f.fid, f.title AS name, f.short_code, f.dir_path AS master_ssid
FROM viz_livespace_files f
WHERE f.tid = %s
  AND f.mime_type = 'spreadsheet'
  AND f.flags = 1
  AND f.version = 1
ORDER BY f.lid, f.title
"""


def spreadsheet_index(tid: int) -> list[dict]:
    """All active spreadsheets for a tenant: name, short_code, master_ssid, lid."""
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute(SS_INDEX_SQL, (tid,))
            return list(cur.fetchall())
