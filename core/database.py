import psycopg2
import psycopg2.extras
from core.config import load_config

_conn = None

def get_connection():
    global _conn
    cfg = load_config()["database"]
    if _conn is None or _conn.closed:
        _conn = psycopg2.connect(
            host=cfg["host"],
            port=cfg["port"],
            database=cfg["database"],
            user=cfg["user"],
            password=cfg["password"]
        )
        _conn.autocommit = False
    return _conn


def get_cursor(dict_cursor: bool = False):
    conn = get_connection()
    if dict_cursor:
        return conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    return conn.cursor()


def commit():
    get_connection().commit()


def rollback():
    get_connection().rollback()


def close():
    global _conn
    if _conn and not _conn.closed:
        _conn.close()
        _conn = None


def run_schema(schema_path: str):
    """Run the schema.sql file to create all tables."""
    with open(schema_path, "r") as f:
        sql = f.read()
    cur = get_cursor()
    cur.execute(sql)
    commit()
    print("✅ Schema applied successfully.")
