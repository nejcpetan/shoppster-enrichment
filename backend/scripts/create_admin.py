"""
Create the initial admin user for a new instance.
Run: python scripts/create_admin.py admin@example.com mypassword "Admin Name"

This script initialises the database itself — run it standalone before starting the server.
"""

import sys
import os

# Ensure backend/ is on the path regardless of where the script is run from
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Load .env so DATABASE_URL / JWT_SECRET etc. are available
from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))

from database.session import init_engine, get_session
from auth.security import hash_password
from sqlalchemy import text
from datetime import datetime


def create_admin(email: str, password: str, name: str = "Admin"):
    if len(password) < 8:
        print("Error: Password must be at least 8 characters")
        sys.exit(1)

    # Initialise engine (creates tables including users if they don't exist)
    init_engine()
    session = get_session()

    # Check if user already exists
    existing = session.execute(
        text("SELECT id FROM users WHERE email = :email"),
        {"email": email.lower().strip()}
    ).fetchone()

    if existing:
        print(f"User {email} already exists")
        session.close()
        return

    hashed = hash_password(password)
    session.execute(
        text("""INSERT INTO users (email, hashed_password, full_name, role, is_active, created_at)
                VALUES (:email, :hash, :name, 'admin', true, :now)"""),
        {"email": email.lower().strip(), "hash": hashed, "name": name, "now": datetime.utcnow()}
    )
    session.commit()
    session.close()
    print(f"Admin user created: {email}")


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: python scripts/create_admin.py <email> <password> [full_name]")
        sys.exit(1)

    email = sys.argv[1]
    password = sys.argv[2]
    name = sys.argv[3] if len(sys.argv) > 3 else "Admin"
    create_admin(email, password, name)
