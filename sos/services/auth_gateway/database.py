from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
import os

# Use the same database as DNY/SOS
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://mumega:mumega@localhost:5432/mumega")

engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
