"""Quick script to delete and recreate the admin user."""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))

from database.session import init_engine, get_session
from auth.security import hash_password
from sqlalchemy import text
from datetime import datetime

EMAIL = "nathan@webfast.si"
PASSWORD = "Adidas_forlife1!"
NAME = "Nathan Petain"

init_engine()
s = get_session()
s.execute(text("DELETE FROM users WHERE email = :e"), {"e": EMAIL})
s.commit()
s.execute(
    text("""INSERT INTO users (email, hashed_password, full_name, role, is_active, created_at)
            VALUES (:e, :h, :n, 'admin', true, :now)"""),
    {"e": EMAIL, "h": hash_password(PASSWORD), "n": NAME, "now": datetime.utcnow()}
)
s.commit()
s.close()
print(f"Admin user reset: {EMAIL}")
