# ⚖️ AI-Assisted Court Case Management Platform

A multi-agent prototype system for US court scheduling that reduces party waiting times, detects lawyer schedule conflicts in real time, and provides AI-generated hearing duration estimates with plain-English rationale.

Built as a dissertation artefact using **LangGraph**, **Claude (Anthropic)**, **FastAPI**, and **Streamlit**.

---

## The Problem

US courts schedule hearings using static dockets with fixed time slots. When one hearing overruns, every subsequent hearing cascades — parties arrive on time and wait hours with no visibility into when they will actually be called. Lawyers appearing across multiple courtrooms have no automated safeguard against being double-booked.

---

## What This System Does

| Feature | Detail |
|---|---|
| **Live queue display** | Public lobby screen showing all 20 courtrooms, case numbers, and ETA ranges |
| **Digital check-in** | Clerk marks lawyer and accused as arrived; system tracks readiness |
| **AI duration estimation** | Claude (Haiku) produces a P25–P75 duration range + plain-English rationale per hearing |
| **Conflict detection** | Rule-based agent scans all 20 courtrooms and flags any lawyer scheduled in two overlapping hearings |
| **ETA orchestration** | Hybrid agent combines queue position, elapsed time, and duration estimates into rolling ETA ranges |
| **Audit trail** | Every agent action is logged with timestamp, inputs, and outputs |

---

## Architecture — Multi-Agent System

Six agents, orchestrated by **LangGraph**, share a typed `CourtState` object. Each courtroom runs as an independent graph instance.

```
Trigger (checkin | tick | overrun | complete)
    │
    ▼
┌─────────────────────────────────────────────────────────┐
│                    LangGraph Router                     │
└──────┬──────────────┬───────────────┬───────────────────┘
       │              │               │
       ▼              ▼               ▼
 CheckInAgent   QueueMonitor    DurationEstimator
 (rule-based)   (rule-based)    (LLM — Claude Haiku)
       │              │               │
       └──────────────┴───────────────┘
                      │
             ┌────────┴────────┐
             ▼                 ▼
    ConflictDetector    ETAOrchestrator
    (rule-based)        (hybrid)
             │                 │
             └────────┬────────┘
                      ▼
                 AuditAgent
                 (rule-based)
                      │
                     END
```

| Agent | Type | Responsibility |
|---|---|---|
| `CheckInAgent` | Rule-based | Records lawyer/accused arrival; marks hearing ready |
| `QueueMonitorAgent` | Rule-based | Detects overruns (>115% of estimate) and completions |
| `DurationEstimatorAgent` | **LLM (Claude)** | Returns P25/P75 range + rationale; falls back to rules if API unavailable |
| `ConflictDetectorAgent` | Rule-based | Cross-courtroom scan for overlapping lawyer slots |
| `ETAOrchestratorAgent` | Hybrid | Rolling ETA recalculation across the full queue |
| `AuditAgent` | Rule-based | Append-only log of every state transition |

---

## Tech Stack

| Layer | Technology |
|---|---|
| Agent orchestration | [LangGraph](https://github.com/langchain-ai/langgraph) |
| LLM | Claude Haiku via [Anthropic SDK](https://github.com/anthropics/anthropic-sdk-python) |
| Database | SQLite + SQLAlchemy |
| API | FastAPI + WebSockets |
| Frontend | Streamlit (multi-page) |
| Evaluation | Python simulation + Matplotlib |
| Language | Python 3.11+ |

---

## Project Structure

```
court_mgmt/
├── Home.py                        # Landing page — role selector + courtroom grid
├── pages/
│   ├── 1_Public_Queue.py          # Lobby screen (all 20 courtrooms, auto-refresh)
│   ├── 2_Clerk_Dashboard.py       # Full docket, check-in controls, conflict resolution
│   └── 3_Lawyer_Schedule.py      # Personal schedule with ETA ranges
├── agents/
│   ├── checkin_agent.py
│   ├── queue_monitor_agent.py
│   ├── duration_estimator_agent.py
│   ├── conflict_detector_agent.py
│   ├── eta_orchestrator_agent.py
│   └── audit_agent.py
├── graph/
│   ├── state.py                   # CourtState TypedDict (shared across all agents)
│   └── workflow.py                # LangGraph StateGraph definition
├── db/
│   ├── models.py                  # SQLAlchemy models (8 tables)
│   ├── database.py                # Engine + session factory
│   └── seed.py                    # Synthetic data generator (conflict-safe scheduling)
├── api/
│   └── main.py                    # FastAPI routes + WebSocket
├── evaluation/
│   ├── simulator.py               # 3-condition day replay engine
│   ├── metrics.py                 # Metric computation
│   ├── conflict_test.py           # Conflict injection and detection accuracy test
│   ├── run_evaluation.py          # Main evaluation runner
│   └── visualize.py               # 7 dissertation-quality charts (Matplotlib)
├── config.py                      # Constants, duration tables, DB URL
├── requirements.txt
├── run.sh                         # Launch helper
└── .env.example                   # Environment variable template
```

---

## Database Schema

```
courtrooms ──< judges
courtrooms ──< hearings >── cases >── accused
                                  >── lawyers
hearings ──< eta_estimates
hearings ──< lawyer_conflicts
           < audit_log
```

**Seed data:** 20 courtrooms · 20 judges · 40 lawyers · ~788 hearings across 5 working days. The seed generator enforces the core constraint: **no lawyer appears in two courtrooms at the same time**.

---

## Quick Start

### 1. Clone and install

```bash
git clone https://github.com/eoogungbemi/court_mgmt.git
cd court_mgmt
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
```

### 2. Configure environment

```bash
cp .env.example .env
# Edit .env — add your ANTHROPIC_API_KEY
```

> The system works without an API key. The `DurationEstimatorAgent` automatically falls back to rule-based estimates if no key is present.

### 3. Seed the database

```bash
./run.sh seed
```

Output confirms 788 hearings across 5 days with zero lawyer conflicts.

### 4. Launch the app

```bash
./run.sh
```

Open **http://localhost:8501** — the home page shows role cards and the live 20-courtroom grid.

---

## Running the App

```bash
./run.sh              # Streamlit app  → http://localhost:8501
./run.sh api          # FastAPI backend → http://localhost:8000/docs
./run.sh eval         # Evaluation suite (rules-only, no LLM calls)
./run.sh eval --llm   # Evaluation suite with LLM duration estimates
```

---

## User Roles

### 📺 Public Queue (`/Public_Queue`)
- All 20 courtrooms in a live grid
- Case numbers only (no names)
- ETA ranges displayed as "Est. 10:15 – 10:45 AM"
- Auto-refreshes every 30 seconds

### 🗂️ Clerk Dashboard (`/Clerk_Dashboard`)
- Select courtroom and date
- Check in lawyer and accused with one click
- Update hearing status (scheduled → in progress → completed)
- Conflict alert banner with per-hearing resolution controls
- Full audit trail per hearing

### 👔 Lawyer Schedule (`/Lawyer_Schedule`)
- Select lawyer by name/bar number
- Day schedule across all courtrooms
- AI-estimated start times with "why this estimate?" rationale
- Conflict warnings with overlap window and resolution instruction

---

## REST API

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/courtrooms` | List all 20 courtrooms |
| `GET` | `/queue/{courtroom_id}` | Queue + ETAs for a courtroom |
| `POST` | `/checkin/{hearing_id}` | Check in lawyer or accused |
| `PATCH` | `/hearing/{hearing_id}/status` | Update hearing status |
| `GET` | `/hearing/{hearing_id}` | Full hearing detail |
| `GET` | `/conflicts` | Active lawyer conflicts |
| `GET` | `/audit/{hearing_id}` | Audit trail for a hearing |
| `WS` | `/live/{courtroom_id}` | WebSocket for live queue updates |

Interactive docs: **http://localhost:8000/docs**

---

## Evaluation Results

Simulation replays 788 hearings across 20 courtrooms × 5 days under three conditions.

### Scheduling conditions compared

| Condition | Description |
|---|---|
| **Baseline** | Static docket (DB scheduled times). No adaptation. |
| **Rules-only** | Docket rebuilt using P75 rule estimates as slot widths. |
| **Full system** | Same as rules, slot widths from LLM estimates where available. |

### Results

| Metric | Baseline | Rules-only | Full system |
|---|---|---|---|
| Avg waiting time | 20.49 min | **3.82 min** | 3.82 min |
| Median waiting time | 4.0 min | **0.0 min** | 0.0 min |
| P90 waiting time | 65.0 min | **10.0 min** | 10.0 min |
| % hearings delayed | 52.2% | **18.5%** | 18.5% |
| Avg idle gap | 3.12 min | 18.03 min | 18.03 min |
| ETA range quality (P25–P75) | — | **69.3%** | 69.3% |

**Wait time reduction vs baseline: 81.4%**

The core tradeoff: p75-buffered scheduling absorbs overruns (less cascading, lower wait) at the cost of more planned idle time between hearings.

### Conflict detection accuracy

10 lawyer schedule conflicts injected across random courtrooms on a test date:

| Metric | Score |
|---|---|
| Precision | **1.000** |
| Recall | **1.000** |
| F1 | **1.000** |

All 10 injected conflicts detected with zero false positives.

### Charts generated

| # | Chart |
|---|---|
| 1 | Average waiting time by condition (bar + SD) |
| 2 | CDF of waiting times |
| 3 | ETA range quality (% actual within P25–P75) |
| 4 | Total courtroom idle time |
| 5 | Waiting time by case type |
| 6 | Cascade effect (wait time vs queue position) |
| 7 | Conflict detection breakdown (pie + precision/recall) |

Run `./run.sh eval` to regenerate all charts into `evaluation/results/`.

---

## Research Questions

| RQ | How the system addresses it |
|---|---|
| **RQ1** — Can AI reduce waiting times while maintaining procedural fairness? | 81.4% avg wait reduction. Judge retains all decision authority; agents only advise on scheduling. |
| **RQ2** — How well can we predict hearing duration using real-time operational signals via LLM? | 69.3% of actual durations fall within the P25–P75 predicted range. Rule-based fallback ensures availability when LLM is unavailable. |
| **RQ3** — What ethical and data protection safeguards are required? | LLM is decision-support only; human override on every status. Audit log is append-only. No personal data sent to external API (case type + complexity only). Hybrid model maintains staff-assisted fallback for digitally excluded users. |

---

## Methodology

Design Science Research Methodology (DSRM) — Peffers et al. (2007):

1. **Problem identification** — Court waiting time and scheduling inefficiency
2. **Solution objectives** — Real-time queue visibility, conflict prevention, ETA estimation
3. **Design and development** — Multi-agent LangGraph system (this artefact)
4. **Demonstration** — Streamlit prototype across 3 user roles
5. **Evaluation** — 788-hearing simulation, conflict detection accuracy test
6. **Communication** — Dissertation write-up

---

## Legal and Ethical Notes

- The LLM component receives **case type, hearing type, and complexity only** — no names, case numbers, or personally identifiable information.
- All agent outputs include a **human review path** — clerks can override any status, resolve any conflict, and the judge makes all case decisions.
- The audit log provides a **tamper-evident record** for every agent action, satisfying accountability requirements.
- This is a **research prototype**. Production deployment in a real court would require a full Data Protection Impact Assessment (DPIA) and legal review.

---

## Licence

MIT — free to use for academic and research purposes.
