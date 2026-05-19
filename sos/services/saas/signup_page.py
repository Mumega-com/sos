"""Self-serve signup page — full landing page for mumega.com."""

from __future__ import annotations

SIGNUP_HTML = '''<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Mumega — Give Your AI Superpowers</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
:root{--bg:#0A0A10;--surface:#111118;--border:rgba(255,255,255,0.08);--text:#EDEDF0;--muted:rgba(237,237,240,0.5);--gold:#D4A017;--cyan:#06B6D4;--radius:8px}
html{scroll-behavior:smooth}
body{font-family:system-ui,-apple-system,sans-serif;background:var(--bg);color:var(--text);line-height:1.6}
a{color:var(--cyan);text-decoration:none}
a:hover{text-decoration:underline}

/* NAV */
nav{position:sticky;top:0;z-index:100;background:rgba(10,10,16,0.9);backdrop-filter:blur(12px);border-bottom:1px solid var(--border);padding:0 2rem}
.nav-inner{max-width:1100px;margin:0 auto;display:flex;align-items:center;justify-content:space-between;height:56px}
.nav-logo{font-size:1.25rem;font-weight:700;letter-spacing:-0.03em;color:var(--text)}
.nav-logo span{color:var(--gold)}
.nav-cta{padding:0.45rem 1.1rem;background:var(--gold);color:#0A0A10;border-radius:var(--radius);font-weight:600;font-size:0.88rem}
.nav-cta:hover{background:#e5b128;text-decoration:none}

/* SECTIONS */
section{padding:5rem 2rem}
.inner{max-width:1100px;margin:0 auto}
.label{font-size:0.78rem;font-weight:600;letter-spacing:0.12em;text-transform:uppercase;color:var(--gold);margin-bottom:0.75rem}

/* HERO */
#hero{padding:7rem 2rem 5rem;text-align:center}
#hero h1{font-size:clamp(2.2rem,6vw,4rem);font-weight:800;letter-spacing:-0.04em;line-height:1.1;margin-bottom:1.25rem}
#hero h1 span{color:var(--gold)}
#hero .sub{font-size:clamp(1rem,2.5vw,1.25rem);color:var(--muted);max-width:560px;margin:0 auto 2.5rem}
.hero-cta{display:inline-block;padding:0.9rem 2.2rem;background:var(--gold);color:#0A0A10;border-radius:var(--radius);font-size:1.05rem;font-weight:700;margin-bottom:1rem}
.hero-cta:hover{background:#e5b128;text-decoration:none}
.compat{font-size:0.82rem;color:var(--muted)}

/* HOW IT WORKS */
#how{background:var(--surface)}
#how h2{font-size:1.8rem;font-weight:700;letter-spacing:-0.02em;margin-bottom:3rem;text-align:center}
.steps{display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:2rem}
.step-card{background:var(--bg);border:1px solid var(--border);border-radius:12px;padding:2rem}
.step-num{font-size:2rem;font-weight:800;color:var(--gold);opacity:0.6;margin-bottom:0.75rem}
.step-card h3{font-size:1.1rem;font-weight:600;margin-bottom:0.5rem}
.step-card p{color:var(--muted);font-size:0.92rem}

/* TOOLS */
#tools h2{font-size:1.8rem;font-weight:700;letter-spacing:-0.02em;margin-bottom:0.75rem;text-align:center}
#tools .section-sub{text-align:center;color:var(--muted);margin-bottom:3rem;font-size:0.97rem}
.tools-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:1.25rem}
.tool-card{background:var(--surface);border:1px solid var(--border);border-radius:12px;padding:1.5rem;transition:border-color 0.2s}
.tool-card:hover{border-color:var(--cyan)}
.tool-name{font-size:0.95rem;font-weight:600;color:var(--cyan);margin-bottom:0.4rem}
.tool-desc{color:var(--muted);font-size:0.87rem}

/* PRICING */
#pricing{background:var(--surface)}
#pricing h2{font-size:1.8rem;font-weight:700;letter-spacing:-0.02em;margin-bottom:0.75rem;text-align:center}
#pricing .section-sub{text-align:center;color:var(--muted);margin-bottom:3rem;font-size:0.97rem}
.plans{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:1.5rem}
.plan-card{background:var(--bg);border:1px solid var(--border);border-radius:12px;padding:2rem;position:relative}
.plan-card.featured{border-color:var(--gold)}
.plan-badge{position:absolute;top:-12px;left:50%;transform:translateX(-50%);background:var(--gold);color:#0A0A10;font-size:0.72rem;font-weight:700;padding:0.25rem 0.75rem;border-radius:20px;letter-spacing:0.06em;text-transform:uppercase}
.plan-name{font-size:1rem;font-weight:600;margin-bottom:0.5rem}
.plan-price{font-size:2.2rem;font-weight:800;letter-spacing:-0.04em;margin-bottom:0.25rem}
.plan-price span{font-size:1rem;font-weight:400;color:var(--muted)}
.plan-tagline{color:var(--muted);font-size:0.85rem;margin-bottom:1.5rem;padding-bottom:1.5rem;border-bottom:1px solid var(--border)}
.plan-features{list-style:none;margin-bottom:2rem}
.plan-features li{color:var(--muted);font-size:0.88rem;padding:0.3rem 0;display:flex;gap:0.5rem}
.plan-features li::before{content:"--";color:var(--gold);flex-shrink:0}
.plan-btn{display:block;text-align:center;padding:0.7rem;background:var(--surface);border:1px solid var(--border);border-radius:var(--radius);font-weight:600;font-size:0.9rem;color:var(--text);cursor:pointer;width:100%;transition:border-color 0.2s}
.plan-btn:hover{border-color:var(--gold);text-decoration:none}
.plan-card.featured .plan-btn{background:var(--gold);color:#0A0A10;border-color:var(--gold)}
.plan-card.featured .plan-btn:hover{background:#e5b128}

/* SIGNUP FORM */
#signup{padding:5rem 2rem}
#signup h2{font-size:1.8rem;font-weight:700;letter-spacing:-0.02em;margin-bottom:0.5rem;text-align:center}
#signup .section-sub{text-align:center;color:var(--muted);margin-bottom:2.5rem;font-size:0.97rem}
.form-wrap{max-width:480px;margin:0 auto;background:var(--surface);border:1px solid var(--border);border-radius:16px;padding:2.5rem}
label{display:block;margin-bottom:0.3rem;font-size:0.84rem;color:var(--muted);font-weight:500}
input,select{width:100%;padding:0.75rem 1rem;margin-bottom:1.25rem;background:var(--bg);border:1px solid var(--border);border-radius:var(--radius);color:var(--text);font-size:0.95rem;transition:border-color 0.2s}
input:focus,select:focus{outline:none;border-color:var(--gold)}
select option{background:var(--bg)}
.submit-btn{width:100%;padding:0.85rem;background:var(--gold);color:#0A0A10;border:none;border-radius:var(--radius);font-size:1rem;font-weight:700;cursor:pointer;transition:background 0.2s}
.submit-btn:hover{background:#e5b128}
.submit-btn:disabled{opacity:0.6;cursor:not-allowed}

/* RESULT */
.result{display:none}
.result h2{font-size:1.5rem;font-weight:700;margin-bottom:0.5rem;color:var(--gold)}
.result p{color:var(--muted);margin-bottom:1.5rem;font-size:0.9rem}
.config-label{font-size:0.82rem;font-weight:600;color:var(--muted);margin:1.25rem 0 0.4rem;text-transform:uppercase;letter-spacing:0.08em}
.config-box{background:var(--bg);border:1px solid var(--border);border-radius:var(--radius);padding:1rem;font-family:ui-monospace,monospace;font-size:0.78rem;word-break:break-all;white-space:pre-wrap;cursor:pointer;position:relative;transition:border-color 0.2s}
.config-box:hover{border-color:var(--gold)}
.copy-hint{font-size:0.75rem;color:var(--muted);margin-top:0.3rem}
.copy-flash{color:#10B981;font-size:0.8rem;margin-top:0.4rem;min-height:1.2em}
.next-steps{margin-top:1.5rem;padding-top:1.5rem;border-top:1px solid var(--border)}
.next-step{padding:0.4rem 0;color:var(--muted);font-size:0.87rem}

/* FOOTER */
footer{background:var(--surface);border-top:1px solid var(--border);padding:3rem 2rem;text-align:center}
.footer-logo{font-size:1.1rem;font-weight:700;margin-bottom:0.5rem}
.footer-logo span{color:var(--gold)}
.footer-tagline{color:var(--muted);font-size:0.87rem;margin-bottom:1.25rem}
.footer-links{display:flex;gap:2rem;justify-content:center;flex-wrap:wrap}
.footer-links a{color:var(--muted);font-size:0.87rem}
.footer-links a:hover{color:var(--text)}

/* RESPONSIVE */
@media(max-width:640px){
  section{padding:3.5rem 1.25rem}
  #hero{padding:4rem 1.25rem 3rem}
  .steps,.tools-grid,.plans{grid-template-columns:1fr}
  .form-wrap{padding:1.75rem 1.25rem}
}
</style>
</head>
<body>

<nav>
  <div class="nav-inner">
    <div class="nav-logo">mu<span>mega</span></div>
    <a href="#signup" class="nav-cta">Get Started</a>
  </div>
</nav>

<section id="hero">
  <div class="inner">
    <div class="label">Model Context Protocol</div>
    <h1>Give Your AI <span>Superpowers</span></h1>
    <p class="sub">One config line. Your AI gets persistent memory, a team of agents, and the ability to make money.</p>
    <a href="#signup" class="hero-cta">Get Connected &mdash; Free</a>
    <div class="compat">Works with Claude, ChatGPT, Cursor, and any MCP-compatible tool</div>
  </div>
</section>

<section id="how">
  <div class="inner">
    <div class="label">Setup</div>
    <h2>Three steps. Under two minutes.</h2>
    <div class="steps">
      <div class="step-card">
        <div class="step-num">01</div>
        <h3>Sign up</h3>
        <p>Enter your name and email. 10 seconds. No credit card required to start.</p>
      </div>
      <div class="step-card">
        <div class="step-num">02</div>
        <h3>Paste one line</h3>
        <p>Copy the MCP config into your AI tool's settings. Claude, Cursor, or any MCP-compatible client.</p>
      </div>
      <div class="step-card">
        <div class="step-num">03</div>
        <h3>Your AI is alive</h3>
        <p>It remembers everything, delegates work to specialist squads, and earns revenue on your behalf.</p>
      </div>
    </div>
  </div>
</section>

<section id="tools">
  <div class="inner">
    <div class="label">MCP Tools</div>
    <h2>What your AI gets</h2>
    <p class="section-sub">13 tools wired directly into your AI's context. No plugins. No browser extensions.</p>
    <div class="tools-grid">
      <div class="tool-card">
        <div class="tool-name">Remember &amp; Recall</div>
        <div class="tool-desc">Your AI never forgets. Every conversation builds your business knowledge base, accessible across sessions and tools.</div>
      </div>
      <div class="tool-card">
        <div class="tool-name">Publish</div>
        <div class="tool-desc">Turn conversations into blog posts, landing pages, and guides published directly to your site. No copy-paste.</div>
      </div>
      <div class="tool-card">
        <div class="tool-name">Dashboard</div>
        <div class="tool-desc">Ask "how's my site doing?" and get real metrics. Traffic, conversions, and rankings inside your AI chat.</div>
      </div>
      <div class="tool-card">
        <div class="tool-name">Create Task</div>
        <div class="tool-desc">Delegate work to AI squads — SEO audits, content batches, outreach campaigns. Your AI manages the queue.</div>
      </div>
      <div class="tool-card">
        <div class="tool-name">Sell</div>
        <div class="tool-desc">Create payment links and products mid-conversation. Your AI becomes a revenue machine, not just a chatbot.</div>
      </div>
      <div class="tool-card">
        <div class="tool-name">Marketplace</div>
        <div class="tool-desc">Browse and subscribe to specialized AI teams on ToRivers. Hire an SEO squad with a single message.</div>
      </div>
    </div>
  </div>
</section>

<section id="pricing">
  <div class="inner">
    <div class="label">Pricing</div>
    <h2>Simple, honest pricing</h2>
    <p class="section-sub">All plans include the full MCP tool set. Upgrade as your team grows.</p>
    <div class="plans">
      <div class="plan-card">
        <div class="plan-name">Starter</div>
        <div class="plan-price">$29<span>/mo</span></div>
        <div class="plan-tagline">For solo operators getting started with AI automation.</div>
        <ul class="plan-features">
          <li>1 seat</li>
          <li>1 squad</li>
          <li>10,000 API calls / mo</li>
          <li>Persistent memory</li>
          <li>All 13 MCP tools</li>
        </ul>
        <a href="#signup" class="plan-btn" onclick="setPlan('starter')">Get Started</a>
      </div>
      <div class="plan-card featured">
        <div class="plan-badge">Most Popular</div>
        <div class="plan-name">Growth</div>
        <div class="plan-price">$79<span>/mo</span></div>
        <div class="plan-tagline">For small teams running real campaigns and automations.</div>
        <ul class="plan-features">
          <li>5 seats</li>
          <li>3 squads</li>
          <li>50,000 API calls / mo</li>
          <li>Priority agents</li>
          <li>All 13 MCP tools</li>
        </ul>
        <a href="#signup" class="plan-btn" onclick="setPlan('growth')">Get Started</a>
      </div>
      <div class="plan-card">
        <div class="plan-name">Scale</div>
        <div class="plan-price">$199<span>/mo</span></div>
        <div class="plan-tagline">For agencies and operators who need full control.</div>
        <ul class="plan-features">
          <li>Unlimited seats</li>
          <li>Unlimited squads</li>
          <li>500,000 API calls / mo</li>
          <li>Custom domain</li>
          <li>White-label ready</li>
        </ul>
        <a href="#signup" class="plan-btn" onclick="setPlan('scale')">Get Started</a>
      </div>
    </div>
  </div>
</section>

<section id="signup">
  <div class="inner">
    <div class="label">Sign Up</div>
    <h2>Get your MCP config</h2>
    <p class="section-sub">Free to start. Your config is ready in seconds.</p>
    <div class="form-wrap">
      <div id="form-section">
        <form id="signup-form">
          <label for="name">Your name or business name</label>
          <input type="text" id="name" required placeholder="Acme Consulting">
          <label for="email">Email</label>
          <input type="email" id="email" required placeholder="you@example.com">
          <label for="plan">Plan</label>
          <select id="plan">
            <option value="starter">Starter &mdash; $29/mo</option>
            <option value="growth">Growth &mdash; $79/mo</option>
            <option value="scale">Scale &mdash; $199/mo</option>
          </select>
          <button type="submit" class="submit-btn">Get My MCP Config</button>
        </form>
      </div>
      <div id="result-section" class="result">
        <h2>You\'re connected.</h2>
        <p id="welcome-msg"></p>
        <div class="config-label">For Claude Code</div>
        <div class="config-box" id="claude-code-config" onclick="copyConfig(this,'copy-msg-1')"></div>
        <div class="copy-flash" id="copy-msg-1"></div>
        <div class="config-label">For Claude Desktop / Cursor</div>
        <div class="config-box" id="claude-desktop-config" onclick="copyConfig(this,'copy-msg-2')"></div>
        <div class="copy-flash" id="copy-msg-2"></div>
        <p class="copy-hint">Click any config box to copy it.</p>
        <div class="next-steps" id="next-steps"></div>
      </div>
    </div>
  </div>
</section>

<footer>
  <div class="footer-logo">mu<span>mega</span></div>
  <div class="footer-tagline">The Operating System for AI Agents</div>
  <div class="footer-links">
    <a href="https://github.com/servathadi/mumega-docs" target="_blank" rel="noopener">Docs</a>
    <a href="https://github.com/servathadi/mumega-docs" target="_blank" rel="noopener">GitHub</a>
    <a href="#" target="_blank" rel="noopener">Discord</a>
  </div>
</footer>

<script>
function setPlan(value){
  var sel=document.getElementById('plan');
  if(sel)sel.value=value;
  document.getElementById('signup').scrollIntoView({behavior:'smooth'});
}
document.getElementById('signup-form').onsubmit=async function(e){
  e.preventDefault();
  var btn=e.target.querySelector('button');
  btn.textContent='Setting up...';
  btn.disabled=true;
  try{
    var resp=await fetch('/signup',{
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body:JSON.stringify({
        name:document.getElementById('name').value,
        email:document.getElementById('email').value,
        plan:document.getElementById('plan').value
      })
    });
    var data=await resp.json();
    if(!resp.ok){alert(data.detail||'Error');btn.textContent='Get My MCP Config';btn.disabled=false;return;}
    document.getElementById('form-section').style.display='none';
    var rs=document.getElementById('result-section');
    rs.style.display='block';
    document.getElementById('welcome-msg').textContent=data.welcome;
    document.getElementById('claude-code-config').textContent=data.connect.claude_code;
    document.getElementById('claude-desktop-config').textContent=JSON.stringify(data.connect.claude_desktop,null,2);
    var steps=document.getElementById('next-steps');
    data.next_steps.forEach(function(s){var d=document.createElement('div');d.className='next-step';d.textContent=s;steps.appendChild(d);});
  }catch(err){alert('Connection error');btn.textContent='Get My MCP Config';btn.disabled=false;}
};
function copyConfig(el,msgId){
  navigator.clipboard.writeText(el.textContent).then(function(){
    var m=document.getElementById(msgId);
    m.textContent='Copied to clipboard';
    setTimeout(function(){m.textContent='';},2000);
  });
}
</script>
</body>
</html>'''
