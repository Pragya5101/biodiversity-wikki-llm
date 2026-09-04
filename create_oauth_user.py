#!/usr/bin/env python3
"""Create (or update the password of) a login account for the OAuth pilot server.

There is no self-service signup -- you (the project owner) run this against
DATABASE_URL for whichever deployment has AUTH_MODE=oauth (biodiversity-mcp-tier3)
to provision an account for each person you want to be able to log in.

Usage:
    python create_oauth_user.py --username alice --password "correct horse battery staple"
"""

import argparse
import os
import sys

import psycopg2
from dotenv import load_dotenv

from oauth_provider import hash_password

load_dotenv()

DATABASE_URL = os.environ.get("DATABASE_URL")
if not DATABASE_URL:
    print("Error: DATABASE_URL environment variable is not set.", file=sys.stderr)
    sys.exit(1)


def main():
    parser = argparse.ArgumentParser(description="Create or update an OAuth login account.")
    parser.add_argument("--username", required=True)
    parser.add_argument("--password", required=True)
    args = parser.parse_args()

    if len(args.password) < 8:
        print("Error: password must be at least 8 characters.", file=sys.stderr)
        sys.exit(1)

    password_hash = hash_password(args.password)
    conn = psycopg2.connect(DATABASE_URL)
    try:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO oauth_users (username, password_hash) VALUES (%s, %s)
                   ON CONFLICT (username) DO UPDATE SET password_hash = EXCLUDED.password_hash""",
                (args.username.strip(), password_hash),
            )
        conn.commit()
        print(f"Account '{args.username}' created/updated.")
    except Exception as e:
        conn.rollback()
        print(f"Failed: {e}", file=sys.stderr)
        sys.exit(1)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
