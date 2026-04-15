"""Self-serve signup page — minimal HTML form."""

from __future__ import annotations

SIGNUP_HTML = '''<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Connect to Mumega</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: system-ui, sans-serif; background: #0A0A10; color: #EDEDF0; min-height: 100vh; display: flex; justify-content: center; align-items: center; }
  .container { max-width: 480px; width: 100%; padding: 2rem; }
  h1 { font-size: 1.5rem; margin-bottom: 0.5rem; color: #D4A017; }
  p { color: rgba(255,255,255,0.55); margin-bottom: 1.5rem; font-size: 0.9rem; }
  label { display: block; margin-bottom: 0.25rem; font-size: 0.85rem; color: rgba(255,255,255,0.7); }
  input, select { width: 100%; padding: 0.75rem; margin-bottom: 1rem; background: #151519; border: 1px solid rgba(255,255,255,0.1); border-radius: 6px; color: #EDEDF0; font-size: 0.95rem; }
  button { width: 100%; padding: 0.75rem; background: #D4A017; color: #0A0A10; border: none; border-radius: 6px; font-size: 1rem; font-weight: 600; cursor: pointer; }
  button:hover { background: #E5B128; }
  button:disabled { opacity: 0.6; cursor: not-allowed; }
  .result { display: none; }
  .config-box { background: #151519; border: 1px solid rgba(255,255,255,0.1); border-radius: 6px; padding: 1rem; margin: 1rem 0; font-family: monospace; font-size: 0.8rem; word-break: break-all; cursor: pointer; white-space: pre-wrap; }
  .config-box:hover { border-color: #D4A017; }
  .step { padding: 0.5rem 0; color: rgba(255,255,255,0.7); font-size: 0.85rem; }
  .copied { color: #10B981; font-size: 0.8rem; }
  h3 { color: rgba(255,255,255,0.85); }
</style>
</head>
<body>
<div class="container">
  <div id="form-section">
    <h1>Connect Your AI to Mumega</h1>
    <p>Give your AI memory, a team, and the ability to make money. Takes 60 seconds.</p>
    <form id="signup-form">
      <label>Your name or business name</label>
      <input type="text" id="name" required placeholder="Acme Consulting">
      <label>Email</label>
      <input type="email" id="email" required placeholder="you@example.com">
      <label>Plan</label>
      <select id="plan">
        <option value="starter">Starter — $29/mo</option>
        <option value="growth">Growth — $79/mo</option>
        <option value="scale">Scale — $199/mo</option>
      </select>
      <button type="submit">Get My MCP Config</button>
    </form>
  </div>
  <div id="result-section" class="result">
    <h1>You're Connected!</h1>
    <p id="welcome-msg"></p>
    <h3 style="margin: 1rem 0 0.5rem; font-size: 0.95rem;">For Claude Code:</h3>
    <div class="config-box" id="claude-code-config" onclick="copyConfig(this)"></div>
    <span class="copied" id="copy-msg"></span>
    <h3 style="margin: 1rem 0 0.5rem; font-size: 0.95rem;">For Claude Desktop / Cursor:</h3>
    <div class="config-box" id="claude-desktop-config" onclick="copyConfig(this)"></div>
    <div id="next-steps"></div>
  </div>
</div>
<script>
document.getElementById('signup-form').onsubmit = async (e) => {
  e.preventDefault();
  const btn = e.target.querySelector('button');
  btn.textContent = 'Setting up...';
  btn.disabled = true;
  try {
    const resp = await fetch('/signup', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({
        name: document.getElementById('name').value,
        email: document.getElementById('email').value,
        plan: document.getElementById('plan').value,
      })
    });
    const data = await resp.json();
    if (!resp.ok) { alert(data.detail || 'Error'); btn.textContent = 'Get My MCP Config'; btn.disabled = false; return; }
    document.getElementById('form-section').style.display = 'none';
    document.getElementById('result-section').style.display = 'block';
    document.getElementById('welcome-msg').textContent = data.welcome;
    document.getElementById('claude-code-config').textContent = data.connect.claude_code;
    document.getElementById('claude-desktop-config').textContent = JSON.stringify(data.connect.claude_desktop, null, 2);
    const steps = document.getElementById('next-steps');
    data.next_steps.forEach(s => { const d = document.createElement('div'); d.className = 'step'; d.textContent = s; steps.appendChild(d); });
  } catch(err) { alert('Connection error'); btn.textContent = 'Get My MCP Config'; btn.disabled = false; }
};
function copyConfig(el) {
  navigator.clipboard.writeText(el.textContent);
  document.getElementById('copy-msg').textContent = 'Copied!';
  setTimeout(() => document.getElementById('copy-msg').textContent = '', 2000);
}
</script>
</body>
</html>'''
