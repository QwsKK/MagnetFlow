# -*- coding: utf-8 -*-
import sqlite3
import sys

sys.stdout.reconfigure(errors="replace")
db = sqlite3.connect(r"D:\Agent\115Translate\data\state.db")
db.row_factory = sqlite3.Row
tables = [r[0] for r in db.execute("select name from sqlite_master where type='table'")]
print("tables:", tables)
for t in tables:
    cols = [c[1] for c in db.execute("pragma table_info(%s)" % t)]
    print(t, cols)
    for r in db.execute("select * from %s limit 10" % t):
        print(dict(r))
