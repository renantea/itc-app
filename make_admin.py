#!/usr/bin/env python3
"""Promote an existing account to organiser.

    .venv/bin/python make_admin.py liam@example.com

There is deliberately no "create the first admin through the web": a signup
form that can mint organisers is a hole, and promoting from the box you already
control is a one-liner. Run it against the account after it has registered
normally.

    .venv/bin/python make_admin.py --list          who is an organiser now
    .venv/bin/python make_admin.py --demote EMAIL  take it away again
"""
import argparse
import sys

from itc.db import connect, now


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("email", nargs="?", help="account to promote")
    ap.add_argument("--demote", metavar="EMAIL", help="back to athlete")
    ap.add_argument("--list", action="store_true", help="list organisers")
    ap.add_argument("--db", default=None, help="database path")
    args = ap.parse_args()

    conn = connect(args.db)
    conn.row_factory = __import__("sqlite3").Row

    if args.list:
        rows = conn.execute(
            "SELECT email, full_name FROM users WHERE role='admin' ORDER BY email"
        ).fetchall()
        if not rows:
            print("No organisers yet.")
        for r in rows:
            print(f"  {r['email']}  ({r['full_name']})")
        return 0

    email = (args.demote or args.email or "").strip().lower()
    if not email:
        ap.print_help()
        return 2
    role = "athlete" if args.demote else "admin"

    user = conn.execute("SELECT user_id, full_name, role FROM users WHERE email = ?",
                        (email,)).fetchone()
    if user is None:
        print(f"No account for {email}. Register it on the site first.",
              file=sys.stderr)
        return 1
    if user["role"] == role:
        print(f"{email} is already {role}.")
        return 0

    conn.execute("UPDATE users SET role = ?, updated_at = ? WHERE user_id = ?",
                 (role, now(), user["user_id"]))
    conn.commit()
    print(f"{user['full_name']} <{email}> is now {role}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
