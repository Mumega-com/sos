# SOS Harness Gaps — What's Missing to Run Real Agent Teams

**Date:** 2026-04-09
**Status:** ALL 10 GAPS CLOSED (2026-04-09). The harness is operational.

### Completed (2026-04-09)
- **Redis AOF** — enabled live + in docker-compose.yml. Streams persist across restarts.
- **Gap 1: Agent Lifecycle Manager** — `sos/services/health/lifecycle.py`, systemd `agent-lifecycle.service`. Polls 60s, detects dead/stuck/compacted, auto-restarts with context.
- **Gap 2: Output Capture** — `sos/services/health/output_capture.py`, systemd `output-capture.service`. Captures tmux diffs 60s, stores logs, parses structured output.
- **Gap 3: Task Result Protocol** — Integrated into output capture. Parses RESULT:/SUMMARY:/VERIFY: → forwards to Squad + Mirror + Redis. Documented in `docs/result-protocol.md`.
- **Gap 4: Restart With Memory** — Rich state snapshots (tasks, output, bus messages, git status, CWD) saved to `~/.sos/state/{agent}.json`. Injected on restart.
- **Gap 5: Cross-Agent Coordination** — `sos/kernel/coordination.py`. DELEGATE/ACK/RESULT protocol with bus + Squad Service as state machine.
- **Gap 6: Execution Proof** — `sos/kernel/verification.py`. Auto-verifies VERIFY: lines (URL check, file exists, git check). Stores proof in Mirror + task result.
- **Gap 7: Budget Enforcement** — `metabolism.can_spend()` wired into `governance.before_action()`. Blocks actions when project over budget.
- **Gap 8: Session Persistence** — Git status, CWD, branch captured in state snapshots every 5 min. Injected on restart.
- **Gap 9: Dead Letter Queue** — Integrated into lifecycle. Messages unread >10 min → redirect to orchestrator. >1 hour → Discord alert.
- **Gap 10: Task Polling Daemon** — `sos/services/health/task_poller.py`, systemd `task-poller.service`. Polls 5 min + real-time event listener for task.assigned.

---

## The Core Problem

Agents are interactive chat sessions, not managed workers.

```
Current:  wake daemon → message in tmux → hope agent reads it → hope it works → ???
Needed:   harness → dispatch task → agent executes → harness captures result → harness verifies → next
```

---

## Gap 1: No Agent Lifecycle Management

**Problem:** Agent hits context limit → dies silently. Nobody knows. Nobody restarts it.
AgentLink died during the session and we only found out by checking tmux manually.

**Impact:** Agents go dark. Work stops. No alert.

**Fix:** Lifecycle manager in Calcifer:
- Poll all agent sessions (tmux + OpenClaw) every 60 seconds
- Detect: dead, compacted, idle, stuck
- On dead: restart with `--continue` flag
- On compacted: send `/compact` or `/clear` + re-inject current task
- On stuck (no output for 30 min): send interrupt, reassign task
- Alert Hadi via bus on any lifecycle event

**Effort:** 1 day

---

## Gap 2: No Output Capture

**Problem:** Agent does work → results live in tmux scroll buffer. Buffer overflows. Gone.
No persistent log of what agents actually produced.

**Impact:** No accountability. Can't audit what happened. Feedback loop has nothing to score.

**Fix:** Output logger per agent:
- Capture tmux pane content every 60 seconds
- Diff against last capture (only store changes)
- Write to `~/.sos/logs/{agent}/{date}.log`
- Parse for structured output (RESULT:, DONE:, ERROR:)
- Forward structured output to Mirror + Squad Service

**Effort:** Half day

---

## Gap 3: No Task Result Collection

**Problem:** Agent completes task → says "done" in tmux. Squad Service doesn't know.
Feedback loop can't score. Manager agent can't track progress.

**Impact:** Flywheel breaks. Tasks stay "in_progress" forever.

**Fix:** Result protocol. Agents must output structured results:
```
RESULT: task_id=xxx status=completed
SUMMARY: Rewrote /pricing page. Bounce rate was 78%.
VERIFY: Check https://viamar.ca/pricing
```
Output logger (Gap 2) parses this and calls:
- `POST /tasks/{id}/complete` on Squad Service
- Stores in Mirror
- Triggers `task.completed` event

**Effort:** Half day (depends on Gap 2)

---

## Gap 4: No Agent Restart With Memory

**Problem:** Agent crashes → watchdog restarts Claude Code → starts BLANK.
Doesn't know what it was working on. Wastes tokens reorienting.

**Impact:** Every restart = cold start. Agent asks "what should I do?" instead of continuing.

**Fix:** Context injection on restart:
- Before restarting, query Mirror: "latest 5 memories for {agent}"
- Query Squad Service: "current in_progress tasks for {agent}"
- Inject as first message: "You were working on: {task}. Last action: {memory}. Continue."
- Use Claude Code `--continue` flag when possible
- Store agent's working state in `~/.sos/state/{agent}.json` (current task, progress, blockers)

**Effort:** 1 day

---

## Gap 5: No Cross-Agent Coordination

**Problem:** Manager can send message to webmaster via bus. But no handshake.
Manager doesn't know if webmaster received, started, or finished.

**Impact:** Agents work in silos. No team coordination.

**Fix:** Task delegation protocol:
```
Manager: DELEGATE task_id=xxx to=viamar-webmaster
         → creates task in Squad Service, assigns to webmaster
         → sends bus message with task_id

Webmaster: ACK task_id=xxx
           → claims task in Squad Service
           → sends bus ACK to manager

Webmaster: RESULT task_id=xxx status=completed
           → completes task in Squad Service
           → sends bus RESULT to manager

Manager: VERIFIED task_id=xxx
         → confirms completion
         → moves to next task
```
Use Squad Service as the state machine. Bus for notifications.

**Effort:** 1 day

---

## Gap 6: No Execution Proof

**Problem:** Agent says "I published a blog post." Did it actually?
No screenshot. No URL check. No diff. Trust without verify.

**Impact:** Phantom completions. Agent hallucinates success.

**Fix:** Verification step after every external action:
- Published page → fetch URL, check HTTP 200, check content matches
- Edited widget → screenshot before/after (SitePilotAI has screenshot tool)
- Sent email → check GHL sent folder
- Created task → query Squad Service for task ID
- Store verification result with the task completion

**Effort:** 1 day

---

## Gap 7: No Budget Enforcement

**Problem:** Metabolism service exists but isn't wired into the execution path.
Agent can burn unlimited tokens. Nothing stops it.

**Impact:** Cost overrun. Free model tier bypassed.

**Fix:** Wire metabolism check into governance:
```python
async def before_action(agent, action, ...):
    # Check budget BEFORE allowing action
    budget = await metabolism.can_spend(tenant, estimated_cost)
    if not budget:
        return {"allowed": False, "reason": "budget_exceeded"}
```
Calcifer checks budget hourly. Alerts at 80% spent. Hard stop at 100%.

**Effort:** Half day

---

## Gap 8: No Session Persistence

**Problem:** tmux dies → everything lost. Agent was mid-task with 10 files open,
understanding a complex problem. Restart = start from zero.

**Impact:** Wasted work. Wasted tokens.

**Fix:** Session state snapshots:
- Every 5 minutes: save current working directory, open files, git status
- Store at `~/.sos/state/{agent}/session.json`
- On restart: inject session state as context
- Mirror stores the "what I was doing" memory automatically (via output logger)

**Effort:** Half day

---

## Gap 9: No Queue for Offline Agents

**Problem:** Send message to agent that's down → Redis stream stores it.
Agent comes back → nobody tells it to check inbox. Message rots.

**Impact:** Messages lost. Coordination breaks.

**Fix:** 
- Wake daemon already handles this partially (checks if agent is at prompt)
- Add: on agent restart, auto-inject "check your inbox" as first message
- Add: if message unread after 10 minutes, redirect to manager or escalate
- Add: dead letter queue — messages older than 1 hour → alert in Discord

**Effort:** Half day

---

## Gap 10: No Multi-Agent Task Handoff

**Problem:** Squad Service has tasks. Agents don't read them autonomously.
Manager creates task, assigns to webmaster. Webmaster never picks it up
because it's sitting in tmux waiting for a human to type something.

**Impact:** Team can't actually work as a team. Just individual agents.

**Fix:** Task polling daemon per agent:
- Every 5 minutes: query Squad Service for assigned tasks
- If new task: inject into agent's tmux as a prompt
- Or: use the event system — `task.assigned` event → wake daemon delivers
- Agent starts working, follows result protocol (Gap 3)

**Effort:** 1 day

---

## The Missing Piece: AgentTaskRunner

All 10 gaps are solved by one component:

```python
class AgentTaskRunner:
    """The harness. Dispatches tasks, captures results, manages lifecycle."""
    
    async def run(self, agent, task):
        # 1. Check budget (Gap 7)
        if not await metabolism.can_spend(tenant, task.estimated_cost):
            return {"status": "blocked", "reason": "budget"}
        
        # 2. Load context from Mirror (Gap 4)
        context = await mirror.recall(f"context for {task.title}")
        
        # 3. Log intent (governance)
        await governance.before_action(agent, task.action, task.target)
        
        # 4. Send task + context to agent (Gap 5)
        await bus.send(agent, f"TASK: {task.title}\nCONTEXT: {context}")
        
        # 5. Wait for result with timeout (Gap 3, 9)
        result = await bus.wait_for_reply(agent, timeout=1800)
        if not result:
            await escalate(agent, task, "timeout")
            return {"status": "timeout"}
        
        # 6. Verify execution (Gap 6)
        verified = await verify_action(task, result)
        
        # 7. Capture output (Gap 2)
        await mirror.store(f"RESULT: {agent} did {task.title}. Verified: {verified}")
        
        # 8. Complete task (Gap 3)
        await squad.complete_task(task.id, result)
        
        # 9. Score in feedback (flywheel)
        await feedback.score(task, result, verified)
        
        # 10. Next task
        return {"status": "completed", "verified": verified}
```

**Total effort to close all gaps: ~7 days.**

**Priority order:**
1. Output capture (Gap 2) — foundation for everything else
2. Result protocol (Gap 3) — agents report structured results
3. Lifecycle management (Gap 1) — agents don't die silently
4. Task polling/handoff (Gap 10) — agents pick up work autonomously
5. Context on restart (Gap 4) — no cold starts
6. Verification (Gap 6) — trust but verify
7. Coordination protocol (Gap 5) — agents work as teams
8. Budget enforcement (Gap 7) — cost control
9. Offline queue (Gap 9) — no lost messages
10. Session persistence (Gap 8) — survive crashes

---

## After All Gaps Closed

```
BEFORE: Agents sit in tmux waiting for humans to type
AFTER:  Harness dispatches tasks, agents execute, results flow back automatically

BEFORE: Agent dies → nobody knows → work stops
AFTER:  Calcifer detects → restarts with context → resumes task

BEFORE: "Did the agent actually do it?" → check tmux manually
AFTER:  Verification step → proof stored in Mirror → feedback scored

BEFORE: Team = independent agents in chat rooms
AFTER:  Team = coordinated workers with handshakes and result protocol
```

The organism goes from "agents that chat" to "agents that work."
