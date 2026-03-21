# CLAUDE.md — JNET Logic Engine

This file provides guidance for AI assistants working in this repository.

## Project Overview

**JNET Logic Engine** is a Python/Streamlit web application that compiles signal-priority logic for LRT (Light Rail Transit) junctions. It translates transition rules from skeleton diagrams (PDFs or Excel) into JNET logic code (strings in a proprietary CSV format used by traffic signal controllers).

**Entry point**: `streamlit run engine_app.py`

---

## Repository Structure

```
JNET-logic-starter/
├── engine_app.py          # Streamlit UI (~680 lines) — 4-step user workflow
├── requirements.txt       # Python dependencies
├── .gitignore
└── engine/                # Logic engine package
    ├── __init__.py
    ├── config.py          # Stage classification & template selector map
    ├── parser.py          # Excel config parser → JunctionConfig dataclass
    ├── compiler.py        # Main orchestrator; dispatches to templates
    ├── demand.py          # Demand string builder with boolean AST logic
    ├── templates.py       # 7 template implementations (A–G)
    └── topology.py        # Graph operations, suffix rules, LRT reachability
```

---

## Architecture

### Data Flow

```
PDF/Excel input
     │
     ▼
engine_app.py (Streamlit UI)
     │  PDF regex parsing or Excel import
     ▼
engine/parser.py → JunctionConfig
     │  dataclass with StageProps, Transition lists
     ▼
engine/compiler.py → iterate transitions → dispatch to template
     │  uses topology.py for graph lookups
     │  uses demand.py for demand string generation
     ▼
engine/templates.py → one JNET logic string per transition
     │
     ▼
Output Excel (via pandas/xlsxwriter)
```

### Layer Responsibilities

| Layer | File(s) | Responsibility |
|-------|---------|----------------|
| UI | `engine_app.py` | Streamlit 4-step workflow, session state, file I/O |
| Parser | `engine/parser.py` | Excel → Python dataclasses |
| Config | `engine/config.py` | Stage type classification, template selection |
| Compiler | `engine/compiler.py` | Orchestration, template dispatch, error capture |
| Templates | `engine/templates.py` | 7 template string builders (A–G) |
| Demand | `engine/demand.py` | Detector-based demand string generation |
| Topology | `engine/topology.py` | Graph building, suffix rules, BFS reachability |

---

## Domain Concepts

### Stage Types
Classified in `engine/config.py`:
- **LRT stages**: match `L\d+` (e.g., `L30`, `L39`)
- **Lig stages**: match `A[3-9]\d` (e.g., `A30`, `A39`)
- **Vehicle stages**: everything else (e.g., `A0`, `B`, `C`)

### Templates (A–G)
Each transition gets one template based on the from/to stage types:

| Template | From → To | Notes |
|----------|-----------|-------|
| A | Vehicle → Vehicle | Two variants: with/without outgoing LRT |
| B | Vehicle → LRT (non-anchor) | |
| C | Vehicle → LRT Anchor | Handles ProgSwitch/Ghost flags |
| D | LRT → Vehicle | Includes CloseL & DQ |
| E | LRT → Lig | |
| F | Lig → Vehicle | Demand-only or NO_LOGIC |
| G | LRT → LRT | EG, WTG, AT_less chaining |

### JNET Functions (uppercase, proprietary)
`GT`, `WTG`, `AT_greater`, `AT_less`, `IsActive`, `IsInactive`, `CloseL`, `LIG`, `EG`, `ProgSwitch`, `Ghost`

### Demand Logic (V20.0 rules) — `engine/demand.py`
1. `IsActive` for the target detector
2. `IsInactive` for higher-priority siblings at the same waterfall level
3. Waterfall rule: `IsInactive` for stages one level below when transitioning up exactly one level
4. Empty input → return `''` (never write `'true'`)

Boolean expressions use Python AST transformation with De Morgan's law and redundancy elimination.

### Topology Conventions — `engine/topology.py`
- In `rest_stages`, `[0]` = "To" stage, `[1:]` = tail-to-anchor path
- Suffix rules: first/last bare element, middle elements suffixed `cpn`/`min`
- LRT reachability uses BFS over the transition graph

---

## Development Setup

```bash
# Install dependencies
pip install -r requirements.txt

# Run the app
streamlit run engine_app.py
```

**Python version**: No explicit constraint; `pandas>=2.0` requires Python 3.8+.

**Dependencies**:
- `streamlit>=1.35.0` — Web UI
- `pandas>=2.0.0` — Data manipulation & Excel I/O
- `openpyxl>=3.1.0` — Excel read
- `xlsxwriter>=3.1.0` — Excel write
- `pdfplumber>=0.10.0` — PDF text extraction

---

## Key Conventions

### Naming
- Stage names: `UPPERCASE` (`A0`, `B`, `L30`)
- Private helpers: `_snake_case`
- Template functions: named by letter, e.g., `template_a(...)`, `template_g(...)`
- JNET functions in output strings: `UPPERCASE` exactly as the controller expects

### Code Patterns
- Use `@dataclass` for config objects (see `parser.py`: `StageProps`, `Transition`, `JunctionConfig`)
- Pure functions preferred in `engine/` — no side effects
- AST-based boolean transformation lives entirely in `demand.py`
- Streamlit session state (`st.session_state`) is the source of truth for UI workflow

### Error Handling
- `compiler.py` captures tracebacks and returns them as strings in the output
- UI (`engine_app.py`) displays compiler errors inline so the user sees context

### Output Format
- Each transition produces a single-line string matching the JNET CSV schema
- Templates are the only place that produce output strings; do not generate JNET syntax elsewhere

---

## Testing

There is currently **no automated test suite**. Validation is done manually via the Streamlit UI:
1. Upload a PDF/Excel configuration
2. Step through the 4-step workflow
3. Inspect the compiled output Excel

When adding or modifying logic in `engine/`, manually verify by running the app and comparing output against expected JNET logic for known test cases.

---

## Git Workflow

- `main` / `master`: stable branch
- Feature branches follow `claude/<description>-<id>` convention
- Commits are GPG-signed (SSH format) with user "Claude"
- `.gitignore` excludes all generated files: `*.xlsx`, `*.csv`, `*.pdf`, `*.docx`, `*.bat`, `__pycache__/`

**Never commit generated output files.** The `.gitignore` enforces this, but be aware.

---

## Areas to Be Careful About

1. **Demand logic is subtle.** The waterfall/sibling rules in `demand.py` have edge cases (see commit history for prior bug fixes). Always trace the AST transformation logic carefully when modifying.
2. **Template dispatch in `compiler.py`** depends on correct stage-type classification. If a stage is misclassified, the wrong template fires silently.
3. **Skeleton cycle selection** prefers cycles with fewest numbered stages — see `engine_app.py` logic.
4. **Session state in Streamlit** can carry stale data across re-runs. When refactoring UI steps, always initialize keys explicitly.
5. **No tests** — regression risk is high. Manual testing required after any change to `engine/`.
