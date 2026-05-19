from fastapi import FastAPI, HTTPException, Request, Depends
from fastapi.responses import RedirectResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session
from .models import Base, SOSConnection, SOSAppCredentials
from .database import get_db, engine
from .oauth_logic import OAuthHandler
from .vault import encrypt_token, decrypt_token
import os
import uuid
import httpx
from datetime import datetime, timedelta

# Initialize
app = FastAPI(title="Mumega Auth Adapter", version="1.0.0")

# CORS for DNY and other apps
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], # Lock this down in prod
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ------------------------------------------------------------------
# 1. THE CONNECT FLOW (Front Door)
# ------------------------------------------------------------------

@app.get("/connect/{provider}")
async def connect_provider(
    provider: str, 
    user_id: str, 
    redirect_back: str,
    db: Session = Depends(get_db)
):
    """
    Step 1: App sends user here. We look up the 'Mumega App' keys 
    and send user to Google/Meta/GitHub.
    """
    creds = db.query(SOSAppCredentials).filter_by(provider=provider).first()
    if not creds:
        raise HTTPException(status_code=404, detail=f"Provider {provider} not configured in Mumega")

    # State passes the user_id and final destination back to us
    state = f"{user_id}|{redirect_back}"
    
    if provider == "google":
        scope = "https://www.googleapis.com/auth/business.manage https://www.googleapis.com/auth/analytics.readonly"
        return RedirectResponse(
            f"https://accounts.google.com/o/oauth2/v2/auth?client_id={creds.client_id}&redirect_uri={creds.redirect_uri}&response_type=code&scope={scope}&access_type=offline&prompt=consent&state={state}"
        )
    
    elif provider == "facebook":
        scope = "pages_show_list,pages_read_engagement,read_insights"
        return RedirectResponse(
            f"https://www.facebook.com/v18.0/dialog/oauth?client_id={creds.client_id}&redirect_uri={creds.redirect_uri}&state={state}&scope={scope}"
        )

    elif provider == "github":
        scope = "repo,user,read:org"
        return RedirectResponse(
            f"https://github.com/login/oauth/authorize?client_id={creds.client_id}&redirect_uri={creds.redirect_uri}&state={state}&scope={scope}"
        )

    raise HTTPException(status_code=400, detail="Provider not supported yet")

# ------------------------------------------------------------------
# 2. THE CALLBACK (The Handshake)
# ------------------------------------------------------------------

@app.get("/callback/{provider}")
async def callback(
    provider: str, 
    code: str, 
    state: str,
    db: Session = Depends(get_db)
):
    """
    Step 2: Google/Meta/GitHub sends user back with a code. 
    We exchange it for tokens and store them ENCRYPTED.
    """
    user_id, final_redirect = state.split("|")
    
    creds = db.query(SOSAppCredentials).filter_by(provider=provider).first()
    handler = OAuthHandler(provider, creds.client_id, creds.client_secret, creds.redirect_uri)
    
    # Exchange code for tokens
    tokens = await handler.exchange_code(code)
    
    if "error" in tokens:
        return JSONResponse(status_code=400, content=tokens)

    # Store in Vault (Encrypted)
    connection = SOSConnection(
        id=uuid.uuid4(),
        user_id=uuid.UUID(user_id),
        provider=provider,
        provider_account_id=str(tokens.get("sub") or tokens.get("id") or tokens.get("user", {}).get("id", "")),
        access_token=encrypt_token(tokens["access_token"]),
        refresh_token=encrypt_token(tokens.get("refresh_token", "")),
        expires_at=datetime.utcnow() + timedelta(seconds=tokens.get("expires_in", 3600*24*30)), # Default 30 days if not provided
        scopes=tokens.get("scope", "").split(",") if provider == "github" else tokens.get("scope", "").split(" "),
        active=True
    )
    
    # Update or Insert
    existing = db.query(SOSConnection).filter_by(user_id=uuid.UUID(user_id), provider=provider).first()
    if existing:
        existing.access_token = connection.access_token
        existing.refresh_token = connection.refresh_token or existing.refresh_token
        existing.expires_at = connection.expires_at
    else:
        db.add(connection)
    
    db.commit()
    
    # Step 3: Send user back to DNY/App
    return RedirectResponse(f"{final_redirect}?status=success&provider={provider}")

# ------------------------------------------------------------------
# 3. THE ADAPTER (The Magic Proxy)
# ------------------------------------------------------------------

@app.post("/proxy/{provider}/{user_id}")
async def proxy_request(
    provider: str,
    user_id: str,
    request: Request,
    db: Session = Depends(get_db)
):
    """
    The Universal Adapter.
    """
    # 1. Get Connection
    conn = db.query(SOSConnection).filter_by(user_id=uuid.UUID(user_id), provider=provider).first()
    if not conn:
        raise HTTPException(status_code=404, detail="Connection not found. Please ask user to connect.")

    # 2. Decrypt Token
    token = decrypt_token(conn.access_token)
    
    # 3. Check Expiry & Refresh
    if conn.expires_at and datetime.utcnow() > conn.expires_at:
        creds = db.query(SOSAppCredentials).filter_by(provider=provider).first()
        handler = OAuthHandler(provider, creds.client_id, creds.client_secret, creds.redirect_uri)
        
        new_tokens = await handler.refresh_token(decrypt_token(conn.refresh_token))
        if "access_token" in new_tokens:
            token = new_tokens["access_token"]
            # Update DB
            conn.access_token = encrypt_token(token)
            if "expires_in" in new_tokens:
                conn.expires_at = datetime.utcnow() + timedelta(seconds=new_tokens["expires_in"])
            db.commit()
    
    # 4. Proxy the Request
    body = await request.json()
    target_url = body.get("url")
    method = body.get("method", "GET")
    data = body.get("data")
    headers = body.get("headers", {})
    
    # Add Authorization header
    headers["Authorization"] = f"Bearer {token}"
    if provider == "github":
        headers["Accept"] = "application/vnd.github+json"
        headers["X-GitHub-Api-Version"] = "2022-11-28"
    
    async with httpx.AsyncClient() as client:
        resp = await client.request(
            method, 
            target_url, 
            json=data, 
            headers=headers
        )
        return resp.json()
