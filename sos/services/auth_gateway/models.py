from sqlalchemy import Column, String, DateTime, JSON, ForeignKey, Boolean
from sqlalchemy.dialects.postgresql import UUID, ARRAY
from sqlalchemy.orm import declarative_base
import uuid
from datetime import datetime

Base = declarative_base()

class SOSConnection(Base):
    __tablename__ = "sos_connections"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), nullable=False, index=True)
    provider = Column(String, nullable=False) # google, facebook, ghl
    provider_account_id = Column(String)
    
    # Encrypted fields
    access_token = Column(String, nullable=False)
    refresh_token = Column(String)
    
    expires_at = Column(DateTime(timezone=True))
    scopes = Column(ARRAY(String))
    metadata_json = Column(JSON, default={})
    
    active = Column(Boolean, default=True)
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow)
    updated_at = Column(DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow)

class SOSAppCredentials(Base):
    """Stores the Master Client ID/Secret for Google/Meta/etc."""
    __tablename__ = "sos_app_credentials"
    
    provider = Column(String, primary_key=True) # google, facebook, ghl
    client_id = Column(String, nullable=False)
    client_secret = Column(String, nullable=False)
    redirect_uri = Column(String, nullable=False)
    config = Column(JSON, default={})
