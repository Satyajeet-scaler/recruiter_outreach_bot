import os
from contextlib import contextmanager
from typing import Any, Iterator
import pymysql

def _get_mysql_kwargs() -> dict[str, Any]:
    host = (os.getenv("MYSQLHOST") or "").strip()
    port = int((os.getenv("MYSQLPORT") or "3306").strip() or "3306")
    user = (os.getenv("MYSQLUSER") or "").strip()
    password = (os.getenv("MYSQLPASSWORD") or "").strip()
    database = (os.getenv("MYSQLDATABASE") or "").strip()
    
    if not all((host, user, password, database)):
        # Fallback to local defaults or raise if production
        if os.getenv("RAILWAY_ENVIRONMENT") == "production":
             raise RuntimeError("Missing MySQL connection env vars: MYSQLHOST, MYSQLUSER, MYSQLPASSWORD, MYSQLDATABASE.")
        
    return {
        "host": host,
        "port": port,
        "user": user,
        "password": password,
        "database": database,
        "charset": "utf8mb4",
        "cursorclass": pymysql.cursors.DictCursor,
        "autocommit": False,
    }

@contextmanager
def db_session() -> Iterator[pymysql.connections.Connection]:
    """Provide a transactional scope around a series of operations."""
    conn = pymysql.connect(**_get_mysql_kwargs())
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
