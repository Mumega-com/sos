"""Login page — inline HTML template."""
from __future__ import annotations

LOGIN_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Mumega — Login</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{background:#0F172A;color:#E2E8F0;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;display:flex;align-items:center;justify-content:center;min-height:100vh}
.login-box{background:#1E293B;border:1px solid #334155;border-radius:12px;padding:40px;width:100%;max-width:400px}
h1{font-size:1.5rem;margin-bottom:8px;color:#F8FAFC}
.subtitle{color:#94A3B8;font-size:0.875rem;margin-bottom:24px}
label{display:block;font-size:0.8rem;color:#94A3B8;margin-bottom:6px;margin-top:16px}
input{width:100%;padding:10px 12px;border:1px solid #334155;border-radius:8px;background:#0F172A;color:#F8FAFC;font-size:0.9rem;outline:none}
input:focus{border-color:#6366F1}
button{width:100%;padding:12px;margin-top:24px;background:#6366F1;color:#fff;border:none;border-radius:8px;font-size:0.95rem;cursor:pointer;font-weight:500}
button:hover{background:#4F46E5}
.error{color:#F87171;font-size:0.85rem;margin-top:12px}
.logo{font-size:2rem;margin-bottom:4px}
</style>
</head>
<body>
<div class="login-box">
  <div class="logo">&#9670;</div>
  <h1>Mumega</h1>
  <p class="subtitle">Tenant Dashboard</p>
  <form method="POST" action="/login">
    <label for="token">Access Token</label>
    <input type="password" id="token" name="token" placeholder="sk-bus-..." required>
    {error}
  </form>
  <button type="submit" onclick="this.closest('.login-box').querySelector('form').submit()">Sign In</button>
</div>
</body>
</html>"""
