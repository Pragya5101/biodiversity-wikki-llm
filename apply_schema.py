#!/usr/bin/env python3
"""Apply schema.sql to DATABASE_URL without touching existing data.

Unlike ingest_animals_data.py (which truncates all wiki tables before
reloading species), this just runs the idempotent CREATE TABLE IF NOT EXISTS /
ALTER TABLE ADD COLUMN IF NOT EXISTS statements in schema.sql -- safe to run
against a database that already has data, e.g. to add the oauth_* tables to
an existing deployment.
"""

import os
import sys

import psycopg2
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.environ.get("DATABASE_URL")
if not DATABASE_URL:
    print("Error: DATABASE_URL environment variable is not set.", file=sys.stderr)
    sys.exit(1)


def main():
    conn = psycopg2.connect(DATABASE_URL)
    try:
        with conn.cursor() as cur:
            with open("schema.sql", "r", encoding="utf-8") as f:
                cur.execute(f.read())
        conn.commit()
        print("Schema applied successfully (no existing data was touched).")
    except Exception as e:
        conn.rollback()
        print(f"Schema apply failed: {e}", file=sys.stderr)
        sys.exit(1)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
