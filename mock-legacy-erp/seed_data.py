from __future__ import annotations

from app.db import init_db


if __name__ == "__main__":
    init_db()
    print("Seeded mock legacy ERP db.sqlite with PO-1001/1002/1003 and cloned parity data.")
