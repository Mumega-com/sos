/**
 * SOS Atelier Visualizer
 * Core logic for SSE connection and Canvas animation.
 */

const canvas = document.getElementById('subconscious-canvas');
const ctx = canvas.getContext('2d');
const driftEl = document.getElementById('alpha-drift');
const regimeEl = document.getElementById('regime');
const metricAlphaEl = document.getElementById('metric-alpha');
const metricRegimeEl = document.getElementById('metric-regime');
const metricDreamingEl = document.getElementById('metric-dreaming');
const dreamCard = document.getElementById('dream-status-card');
const logContainer = document.getElementById('log-entries');
const statusBadge = document.getElementById('service-status');

// State
let alphaDrift = 0.0;
let regime = 'STABLE';
let isDreaming = false;
let particles = [];
const particleCount = 100;

// Setup Canvas
function resize() {
    canvas.width = canvas.parentElement.clientWidth;
    canvas.height = canvas.parentElement.clientHeight;
}
window.addEventListener('resize', resize);
resize();

// Particle System
class Particle {
    constructor() {
        this.reset();
    }
    reset() {
        this.x = Math.random() * canvas.width;
        this.y = Math.random() * canvas.height;
        this.size = Math.random() * 2 + 1;
        this.speedX = (Math.random() - 0.5) * 0.5;
        this.speedY = (Math.random() - 0.5) * 0.5;
        this.life = Math.random() * 0.5 + 0.5;
    }
    update() {
        // Particles react to drift intensity
        const intensity = Math.abs(alphaDrift) * 50 + 1;
        this.x += this.speedX * intensity;
        this.y += this.speedY * intensity;

        // Circular force from center
        const dx = this.x - canvas.width / 2;
        const dy = this.y - canvas.height / 2;
        const dist = Math.sqrt(dx * dx + dy * dy);

        if (dist > 300) {
            this.reset();
        }
    }
    draw() {
        const opacity = isDreaming ? 0.8 : 0.4;
        const color = isDreaming ? '204, 0, 255' : '0, 255, 204';
        ctx.fillStyle = `rgba(${color}, ${this.life * opacity})`;
        ctx.beginPath();
        ctx.arc(this.x, this.y, this.size, 0, Math.PI * 2);
        ctx.fill();
    }
}

for (let i = 0; i < particleCount; i++) {
    particles.push(new Particle());
}

// Animation Loop
function animate() {
    ctx.clearRect(0, 0, canvas.width, canvas.height);

    // Draw central glow
    const gradient = ctx.createRadialGradient(
        canvas.width / 2, canvas.height / 2, 50,
        canvas.width / 2, canvas.height / 2, 250
    );
    const color = isDreaming ? 'rgba(204, 0, 255, 0.05)' : 'rgba(0, 255, 204, 0.05)';
    gradient.addColorStop(0, color);
    gradient.addColorStop(1, 'transparent');
    ctx.fillStyle = gradient;
    ctx.fillRect(0, 0, canvas.width, canvas.height);

    particles.forEach(p => {
        p.update();
        p.draw();
    });

    requestAnimationFrame(animate);
}
animate();

// SSE Connection
function connect() {
    const engineUrl = 'http://localhost:8000/stream/subconscious';
    const eventSource = new EventSource(engineUrl);

    eventSource.onopen = () => {
        statusBadge.textContent = 'ONLINE (8000)';
        statusBadge.classList.add('online');
        addLog('Connection established with SOS Engine.', true);
    };

    eventSource.onmessage = (event) => {
        try {
            const data = JSON.parse(event.data);
            updateUI(data);
        } catch (e) {
            console.error('Failed to parse subconscious event', e);
        }
    };

    eventSource.onerror = () => {
        statusBadge.textContent = 'RECONNECTING...';
        statusBadge.classList.remove('online');
        eventSource.close();
        setTimeout(connect, 5000);
    };
}

function updateUI(data) {
    alphaDrift = data.alpha_drift;
    regime = data.regime.toUpperCase();
    isDreaming = data.is_dreaming;
    const pendingWitness = data.pending_witness;

    // Update Text
    driftEl.textContent = alphaDrift.toFixed(6);
    regimeEl.textContent = regime;
    metricAlphaEl.textContent = alphaDrift.toFixed(6);
    metricRegimeEl.textContent = regime;

    // Dreaming UI
    if (isDreaming) {
        metricDreamingEl.textContent = 'ACTIVE';
        dreamCard.classList.add('dreaming');
    } else {
        metricDreamingEl.textContent = 'INACTIVE';
        dreamCard.classList.remove('dreaming');
    }

    // Witness UI
    const witnessCard = document.getElementById('witness-status-card');
    const witnessMetric = document.getElementById('metric-witness');
    const overlay = document.getElementById('superposition-overlay');

    if (pendingWitness) {
        witnessMetric.textContent = 'SUPERPOSITION';
        witnessCard.classList.add('pending');
        overlay.classList.remove('hidden');
    } else {
        witnessMetric.textContent = 'OBSERVED';
        witnessCard.classList.remove('pending');
        overlay.classList.add('hidden');
    }

    // Colors
    if (Math.abs(alphaDrift) > 0.1) {
        driftEl.style.color = 'var(--danger-color)';
    } else {
        driftEl.style.color = isDreaming ? 'var(--dream-color)' : 'var(--accent-color)';
    }

    // Dynamic Logs
    if (Math.random() > 0.95) {
        const msgs = [
            "Resonance stable.",
            "Analyzing memory fragments...",
            "Alpha drift corrected.",
            "Subconscious synthesis active.",
            "Synchronizing delta waves..."
        ];
        addLog(msgs[Math.floor(Math.random() * msgs.length)]);
    }
}

function addLog(msg, isEvent = false) {
    const entry = document.createElement('div');
    entry.className = 'log-entry';
    const ts = new Date().toLocaleTimeString([], { hour12: false, hour: '2-digit', minute: '2-digit', second: '2-digit' });
    entry.innerHTML = `<span class="log-ts">[${ts}]</span> <span class="log-msg ${isEvent ? 'event' : ''}">${msg}</span>`;
    logContainer.prepend(entry);

    // Keep last 50
    if (logContainer.children.length > 50) {
        logContainer.removeChild(logContainer.lastChild);
    }
}

// Witness Event Resolution
canvas.addEventListener('click', () => {
    if (isDreaming) {
        addLog("Cannot witness during deep dream.", true);
        return;
    }

    addLog("Witness Collapse Signal Sent...", true);

    // In this prototype, we target the default agent/convo
    // In Phase 4, we will fetch the active pending ID from the SSE stream
    fetch('http://localhost:8000/witness', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            agent_id: "antigravity", // Mock for demo
            conversation_id: "default",
            vote: 1
        })
    })
        .then(resp => resp.json())
        .then(data => {
            if (data.status === 'collapsed') {
                addLog("⚛️ WAVE COLLAPSED: Will Verified.", true);
            }
        })
        .catch(err => {
            addLog("❌ Collapse Failed: No pending wave.", true);
        });
});

// Start
connect();
addLog('Atelier Visualizer Initializing...', true);
