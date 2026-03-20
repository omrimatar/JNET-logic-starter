"""
engine_app.py — JNET Logic Engine (Route Tool + Deterministic Compiler, combined)

Flow:
  Step 1 → Upload PDFs  → parse transitions & stages
  Step 2 → Define anchors, find maximum skeleton
  Step 3 → Confirm skeleton, auto-generate route & stage tables
  Step 4 → Review / edit tables → Compile → download 4-sheet Excel
"""

import io
import re

import pdfplumber
import streamlit as st
import pandas as pd

from engine.config import is_lrt, is_lig
from engine.parser import parse_excel, StageProps
from engine.compiler import compile_junction
from engine.demand import build_demand


# ══════════════════════════════════════════════════════════════════════════════
# ROUTE LOGIC HELPERS  (ported from route_app.py)
# ══════════════════════════════════════════════════════════════════════════════

def parse_interstages_pdf(pdf_file) -> tuple[list, list]:
    transitions: set[tuple[str, str]] = set()
    all_stages: set[str] = set()
    with pdfplumber.open(pdf_file) as pdf:
        for page in pdf.pages:
            text = page.extract_text()
            if not text:
                continue
            for line in text.split('\n'):
                m = re.search(r"([a-zA-Z0-9]+)->([a-zA-Z0-9]+)", line)
                if m:
                    s_from, s_to = m.group(1), m.group(2)
                    transitions.add((s_from, s_to))
                    all_stages.add(s_from)
                    all_stages.add(s_to)
    return list(transitions), sorted(all_stages)


def find_longest_cycle(transitions: list, anchor: str) -> list[list[str]]:
    """DFS to find all simple cycles through anchor (vehicle stages only)."""
    graph: dict[str, list[str]] = {}
    valid_nodes: set[str] = set()
    for s_from, s_to in transitions:
        if is_lrt(s_from) or is_lig(s_from) or is_lrt(s_to) or is_lig(s_to):
            continue
        graph.setdefault(s_from, []).append(s_to)
        valid_nodes.add(s_from)
        valid_nodes.add(s_to)

    if anchor not in valid_nodes:
        return []

    paths: list[list[str]] = []

    def dfs(node: str, path: list[str], visited: set[str]) -> None:
        if node == anchor and len(path) > 1:
            paths.append(list(path))
            return
        if node in visited or node not in graph:
            return
        visited.add(node)
        for nb in graph[node]:
            dfs(nb, path + [nb], visited)
        visited.remove(node)

    for nb in graph.get(anchor, []):
        dfs(nb, [anchor, nb], {anchor})

    def _score(path: list[str]) -> tuple[int, int]:
        # Primary: longest path first (more stages covered).
        # Secondary: fewest numbered intermediate stages first.
        # "Numbered" = stage name contains a digit, excluding the anchor
        # which sits at path[0] and path[-1] and is always expected to have one.
        intermediates = path[1:-1]
        numbered = sum(1 for s in intermediates if any(c.isdigit() for c in s))
        return (-len(path), numbered)

    paths.sort(key=_score)
    return paths


def calculate_rest_of_skeleton(
    s_from: str, s_to: str,
    max_skeleton: list[str],
    v_anchor: str, lrt_anchor: str,
    all_transitions: list[tuple[str, str]],
) -> str:
    def get_suffix(stage: str) -> str | None:
        if stage not in max_skeleton:
            return None
        idx = max_skeleton.index(stage)
        return "-".join(max_skeleton[idx:])

    # Rule: To is an anchor → end of skeleton
    if s_to in (v_anchor, lrt_anchor):
        return "end of skeleton"

    # Special: A01 replaces anchor at start
    if s_to == "A01":
        temp = list(max_skeleton)
        if temp[0] == v_anchor:
            temp[0] = "A01"
        return "-".join(temp)

    # To is in max skeleton → return suffix from that point
    if s_to in max_skeleton:
        suffix = get_suffix(s_to)
        if suffix:
            return suffix

    # To is LRT or Lig → find earliest re-entry into skeleton
    if is_lrt(s_to) or is_lig(s_to):
        next_hops = [dest for (src, dest) in all_transitions if src == s_to]
        valid_hops = [h for h in next_hops if h in max_skeleton]
        if valid_hops:
            def sort_key(stage: str) -> int:
                return len(max_skeleton) if stage == v_anchor else (
                    max_skeleton.index(stage) if stage in max_skeleton else 999
                )
            valid_hops.sort(key=sort_key)
            best = valid_hops[0]
            if best == v_anchor:
                return f"{s_to}-{v_anchor}"
            suffix = get_suffix(best)
            return f"{s_to}-{suffix}" if suffix else f"{s_to}-{v_anchor}"

    # Fallback: all next hops lead to anchor
    next_hops = [dest for (src, dest) in all_transitions if src == s_to]
    if next_hops and all(d in (v_anchor, lrt_anchor) for d in next_hops):
        return f"{s_to}-{v_anchor}"

    return "Check Manually"


# ══════════════════════════════════════════════════════════════════════════════
# EXCEL BUILDERS
# ══════════════════════════════════════════════════════════════════════════════

def _write_config_sheets(writer: pd.ExcelWriter,
                         v_anchor: str, lrt_anchor: str, final_skel: list[str],
                         df_routes: pd.DataFrame, df_props: pd.DataFrame) -> None:
    """Write the three config sheets into an open ExcelWriter."""
    info_df = pd.DataFrame({
        "Parameter": ["Vehicle Anchor", "LRT Anchor", "Maximum Skeleton"],
        "Value":     [v_anchor, lrt_anchor, " - ".join(final_skel)],
    })
    info_df.to_excel(writer, index=False, sheet_name="General Info")
    df_routes.to_excel(writer, index=False, sheet_name="Inter-Stages")
    df_props.to_excel(writer, index=False, sheet_name="Stages Properties")
    for sheet in writer.sheets.values():
        sheet.set_column(0, 5, 22)


def build_config_excel(v_anchor, lrt_anchor, final_skel, df_routes, df_props) -> bytes:
    """3-sheet skeleton config Excel (used internally to feed the engine)."""
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine='xlsxwriter') as writer:
        _write_config_sheets(writer, v_anchor, lrt_anchor, final_skel, df_routes, df_props)
    return buf.getvalue()


def build_output_excel(v_anchor, lrt_anchor, final_skel,
                       df_routes, df_props, logic_rows: list[dict]) -> bytes:
    """4-sheet output Excel: config sheets + JNET Logic."""
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine='xlsxwriter') as writer:
        _write_config_sheets(writer, v_anchor, lrt_anchor, final_skel, df_routes, df_props)
        pd.DataFrame(logic_rows).to_excel(writer, index=False, sheet_name="JNET Logic")
        logic_sheet = writer.sheets["JNET Logic"]
        logic_sheet.set_column(0, 3, 16)   # #, From, To, Template
        logic_sheet.set_column(4, 4, 90)   # JNET Logic Code — wide
    return buf.getvalue()


# ══════════════════════════════════════════════════════════════════════════════
# EXCEL LOAD HELPER  (populate session state from an existing 4-sheet output)
# ══════════════════════════════════════════════════════════════════════════════

def _load_from_excel(file) -> bool:
    """
    Parse an existing 4-sheet JNET output Excel and populate session state.
    Jumps directly to Step 4 on success.  Returns False on error.
    """
    try:
        xl = pd.ExcelFile(file)

        # ── General Info ──────────────────────────────────────────────────────
        gi = xl.parse('General Info')
        gi_map: dict[str, str] = {}
        for _, row in gi.iterrows():
            vals = [str(v).strip() for v in row
                    if str(v).strip() not in ('', 'nan')]
            if len(vals) >= 2:
                gi_map[vals[0]] = vals[1]

        v_anchor   = gi_map.get('Vehicle Anchor', '')
        lrt_anchor = gi_map.get('LRT Anchor', '')
        skel_str   = gi_map.get('Maximum Skeleton', '')
        final_skel = [s.strip() for s in skel_str.replace('→', '-').split('-')
                      if s.strip()]

        # ── Inter-Stages ──────────────────────────────────────────────────────
        is_df = xl.parse('Inter-Stages').dropna(how='all')

        transitions: list[tuple[str, str]] = []
        all_stages_set: set[str] = set()
        for _, row in is_df.iterrows():
            from_s = str(row.get('From Stage', '')).strip()
            to_s   = str(row.get('To Stage',   '')).strip()
            if from_s and to_s and 'nan' not in (from_s, to_s):
                transitions.append((from_s, to_s))
                all_stages_set.update([from_s, to_s])

        # Compact To Stage → Rest of Skeleton map (first occurrence wins)
        to_rest_map: dict[str, str] = {}
        for _, row in is_df.iterrows():
            to_s = str(row.get('To Stage', '')).strip()
            rest = str(row.get('Rest of Skeleton', '')).strip()
            if to_s and to_s != 'nan' and to_s not in to_rest_map:
                to_rest_map[to_s] = '' if rest == 'nan' else rest

        df_to_rest = pd.DataFrame([
            {'To Stage': to, 'Rest of Skeleton': rest}
            for to, rest in sorted(to_rest_map.items())
        ])

        # Full routes table (keep Demand Override column if present)
        keep_cols = ['From Stage', 'To Stage', 'Rest of Skeleton']
        if 'Demand Override' in is_df.columns:
            keep_cols.append('Demand Override')
        df_routes = is_df[keep_cols].copy()
        df_routes = df_routes[
            df_routes['From Stage'].astype(str).str.strip()
            .isin({fs for fs, _ in transitions})
        ].reset_index(drop=True)

        # ── Stages Properties ─────────────────────────────────────────────────
        df_props = xl.parse('Stages Properties').dropna(how='all').reset_index(drop=True)

        # ── Source name from filename ──────────────────────────────────────────
        fname = getattr(file, 'name', 'junction')
        m = re.search(r"[A-Z]{2}\d{2}", fname)
        source_name = m.group(0) if m else re.sub(r"[^\w]", "_", fname)

        # ── Populate session state ─────────────────────────────────────────────
        st.session_state.transitions      = transitions
        st.session_state.all_stages       = sorted(all_stages_set)
        st.session_state.v_anchor         = v_anchor
        st.session_state.lrt_anchor       = lrt_anchor
        st.session_state.final_skel       = final_skel
        st.session_state.max_skel_options = [final_skel]
        st.session_state.df_to_rest       = df_to_rest
        st.session_state.df_routes        = df_routes
        st.session_state.df_props         = df_props
        st.session_state.source_name      = source_name
        st.session_state.steps            = 4
        return True

    except Exception as exc:
        st.error(f"Failed to load Excel: {exc}")
        with st.expander("Full traceback"):
            import traceback
            st.code(traceback.format_exc())
        return False


# ══════════════════════════════════════════════════════════════════════════════
# DEMAND-PREVIEW HELPERS
# ══════════════════════════════════════════════════════════════════════════════

def _props_from_df(df: pd.DataFrame) -> dict[str, StageProps]:
    """Convert the Stage Properties DataFrame into a StageProps dict."""
    props: dict[str, StageProps] = {}
    for _, row in df.iterrows():
        name = str(row.get('Stage', '')).strip()
        if not name:
            continue
        det = str(row.get('Detectors', '')).strip()
        det = '' if det in ('nan', 'None') else det
        wl = row.get('Waterfall Level')
        sp = row.get('Sibling Priority')
        props[name] = StageProps(
            name             = name,
            min_type         = str(row.get('Minimum Type', 'min')).strip() or 'min',
            detector         = det,
            waterfall_level  = float(wl) if pd.notna(wl) and wl is not None else None,
            sibling_priority = float(sp) if pd.notna(sp) and sp is not None else None,
        )
    return props


def _demand_preview(to_s: str, from_s: str, stage_props: dict) -> str:
    """Return the auto-computed demand string, or an error marker on failure."""
    try:
        return build_demand(to_s, from_s, stage_props)
    except Exception as exc:
        return f"⚠ {exc}"


# ══════════════════════════════════════════════════════════════════════════════
# APP
# ══════════════════════════════════════════════════════════════════════════════

st.set_page_config(page_title="JNET Logic Engine", page_icon="🚦", layout="wide")
st.title("🚦 JNET Logic Engine")
st.caption(
    "Upload inter-stages & skeleton PDFs → define anchors → review routes → "
    "compile → download Excel with JNET Logic sheet."
)

with st.sidebar:
    st.header("⚙️ About")
    st.markdown(
        "Encodes all JNET V20.0 template rules (A–G) directly in Python. "
        "Results are instant and deterministic — no API key required."
    )
    st.divider()
    st.markdown("**Output:** 4-sheet Excel  \n`General Info · Inter-Stages · Stages Properties · JNET Logic`")

# ── Session state defaults ─────────────────────────────────────────────────────
_defaults = dict(
    steps=1,
    transitions=[],
    all_stages=[],
    max_skel_options=[],
    df_to_rest=pd.DataFrame(),   # compact: unique To Stage → Rest of Skeleton
    df_routes=pd.DataFrame(),    # full: every (From, To, Rest) — derived, not edited
    df_props=pd.DataFrame(),
    v_anchor='',
    lrt_anchor='',
    final_skel=[],
    source_name='junction',
)
for k, v in _defaults.items():
    if k not in st.session_state:
        st.session_state[k] = v


# ══ STEP 1 — Upload Files ══════════════════════════════════════════════════════
st.header("1 — Upload Files")

input_mode = st.radio(
    "Input method:",
    ["Build from PDFs", "Load from existing Excel"],
    horizontal=True,
    key="input_mode",
)

if input_mode == "Build from PDFs":
    c1, c2 = st.columns(2)
    with c1:
        inter_file = st.file_uploader("Inter-stages (PDF)", type='pdf')
    with c2:
        skel_file = st.file_uploader("Skeletons (PDF)", type='pdf')

    if inter_file and skel_file and st.session_state.steps == 1:
        if st.button("Parse Files", type="primary"):
            with st.spinner("Reading PDFs…"):
                trans, stages = parse_interstages_pdf(inter_file)
            st.session_state.transitions = trans
            st.session_state.all_stages = stages
            m = re.search(r"[A-Z]{2}\d{2}", inter_file.name)
            st.session_state.source_name = m.group(0) if m else re.sub(r"[^\w]", "_", inter_file.name)
            st.session_state.steps = 2
            st.rerun()

else:  # Load from existing Excel
    st.markdown(
        "Upload a previously generated **4-sheet JNET output Excel** "
        "(`General Info · Inter-Stages · Stages Properties · JNET Logic`).  \n"
        "All configuration will be restored and you can edit or re-compile directly."
    )
    xl_file = st.file_uploader("JNET Output Excel", type=['xlsx'], key="xl_upload")

    if xl_file and st.session_state.steps == 1:
        if st.button("Load Excel", type="primary"):
            with st.spinner("Loading…"):
                if _load_from_excel(xl_file):
                    st.rerun()


# ══ STEP 2 — Anchors ══════════════════════════════════════════════════════════
if st.session_state.steps >= 2:
    st.divider()
    st.header("2 — Define Anchors")

    stages = st.session_state.all_stages
    def_veh = "A0"  if "A0"  in stages else stages[0]
    def_lrt = "L39" if "L39" in stages else stages[-1]

    col1, col2 = st.columns(2)
    with col1:
        v_anchor = st.selectbox(
            "Vehicle Anchor", stages,
            index=stages.index(def_veh) if def_veh in stages else 0,
        )
    with col2:
        lrt_anchor = st.selectbox(
            "LRT Anchor", stages,
            index=stages.index(def_lrt) if def_lrt in stages else 0,
        )

    if st.session_state.steps == 2:
        if st.button("Find Maximum Skeleton", type="primary"):
            opts = find_longest_cycle(st.session_state.transitions, v_anchor)
            if not opts:
                st.error(f"No vehicle cycle found starting from '{v_anchor}'.")
            else:
                st.session_state.max_skel_options = opts
                st.session_state.v_anchor   = v_anchor
                st.session_state.lrt_anchor = lrt_anchor
                st.session_state.steps = 3
                st.rerun()


# ══ STEP 3 — Confirm Skeleton ══════════════════════════════════════════════════
if st.session_state.steps >= 3:
    st.divider()
    st.header("3 — Select Maximum Skeleton")

    opt_strings = [" - ".join(p) for p in st.session_state.max_skel_options] + ["Manual Entry"]
    selected = st.radio("Detected cycles (longest first):", opt_strings, horizontal=True)

    final_skel: list[str] = []
    if selected == "Manual Entry":
        manual = st.text_input("Enter skeleton stages (comma-separated, e.g. A0, B, C, A0)")
        if manual:
            final_skel = [s.strip() for s in manual.split(',')]
    else:
        final_skel = selected.split(" - ")

    if final_skel and st.session_state.steps == 3:
        if st.button("Generate Configuration Tables", type="primary"):
            va = st.session_state.v_anchor
            la = st.session_state.lrt_anchor

            # Build unique To Stage → Rest of Skeleton map.
            # Rest of skeleton is determined by To Stage alone (From is irrelevant).
            to_rest_map: dict[str, str] = {}
            for s_from, s_to in sorted(st.session_state.transitions):
                if s_to not in to_rest_map:
                    to_rest_map[s_to] = calculate_rest_of_skeleton(
                        s_from, s_to, final_skel, va, la,
                        st.session_state.transitions,
                    )

            # Compact editor table: one row per unique To Stage
            st.session_state.df_to_rest = pd.DataFrame([
                {"To Stage": to, "Rest of Skeleton": rest}
                for to, rest in sorted(to_rest_map.items())
            ])

            # Full routes table derived from the map (used by the engine)
            st.session_state.df_routes = pd.DataFrame([
                {"From Stage": s_from, "To Stage": s_to,
                 "Rest of Skeleton": to_rest_map[s_to]}
                for s_from, s_to in sorted(st.session_state.transitions)
            ])

            props_data = [
                {
                    "Stage": s,
                    "Minimum Type": "min",
                    "Detectors": "",
                    "Waterfall Level": None,
                    "Sibling Priority": None,
                }
                for s in sorted(st.session_state.all_stages)
                if not is_lrt(s) and not is_lig(s)
            ]
            st.session_state.df_props  = pd.DataFrame(props_data)
            st.session_state.final_skel = final_skel
            st.session_state.steps = 4
            st.rerun()


# ══ STEP 4 — Review, Edit & Compile ═══════════════════════════════════════════
if st.session_state.steps == 4:
    st.divider()
    st.header("4 — Review & Compile")

    # ── Rest of Skeleton — compact editor (one row per unique To Stage) ──────
    st.subheader("Rest of Skeleton  ·  by Target Stage")
    st.markdown(
        "Each **To Stage** determines the rest of skeleton for all transitions "
        "that arrive at it. Edit only the rows that say **Check Manually**."
    )

    n_manual = (
        st.session_state.df_to_rest["Rest of Skeleton"]
        .str.strip().str.lower().eq("check manually").sum()
    )
    if n_manual:
        st.warning(f"{n_manual} target stage(s) need manual review — fix before compiling.")

    edited_to_rest = st.data_editor(
        st.session_state.df_to_rest,
        use_container_width=True,
        num_rows="fixed",
        key="editor_to_rest",
        column_config={
            "To Stage":         st.column_config.TextColumn(disabled=True),
            "Rest of Skeleton": st.column_config.TextColumn(
                width="large",
                help="Path from this stage back to the nearest Anchor, dash-separated. E.g. B-C-A0",
            ),
        },
    )

    # Derive full (From, To, Rest) table from the edited map
    _to_rest_map = dict(zip(edited_to_rest["To Stage"], edited_to_rest["Rest of Skeleton"]))
    _basic_routes = pd.DataFrame([
        {"From Stage": s_from, "To Stage": s_to,
         "Rest of Skeleton": _to_rest_map.get(s_to, "Check Manually")}
        for s_from, s_to in sorted(st.session_state.transitions)
    ])

    with st.expander(f"Full Inter-Stages table ({len(_basic_routes)} rows) — read-only preview"):
        st.dataframe(_basic_routes, use_container_width=True)

    st.divider()

    # ── Stage Properties table ────────────────────────────────────────────────
    st.subheader("Stages Properties")
    st.markdown(
        "Fill in **Detectors**, **Waterfall Level** (0 = highest), and "
        "**Sibling Priority** (1 = highest) for each vehicle stage.  \n"
        "Detector expressions support boolean syntax: `Pc`, `D6 or D10`, "
        "`(D2 or Pa) and not Pb`  (case-insensitive AND/OR/NOT)."
    )
    edited_props = st.data_editor(
        st.session_state.df_props,
        use_container_width=True,
        key="editor_props",
        column_config={
            "Stage":          st.column_config.TextColumn(disabled=True),
            "Minimum Type":   st.column_config.SelectboxColumn(
                options=["min", "cpn", "saf"], default="min", required=True,
            ),
            "Detectors":      st.column_config.TextColumn(
                help="Single: Pc  |  OR: D6 or D10  |  Complex: (D2 or Pa) and not Pb"
            ),
            "Waterfall Level": st.column_config.NumberColumn(min_value=0, step=1, help="0 = highest"),
            "Sibling Priority": st.column_config.NumberColumn(min_value=1, step=1, help="1 = highest"),
        },
    )

    st.divider()

    # ── Demand Override table ─────────────────────────────────────────────────
    st.subheader("Demand Override  ·  per Transition (optional)")
    st.markdown(
        "**Demand Preview** shows the auto-computed demand for each transition "
        "(updates live as you edit Stage Properties above).  \n"
        "Fill **Demand Override** only when you want to replace the auto value for "
        "a specific transition — leave blank to use auto."
    )

    _stage_props = _props_from_df(edited_props)
    _demand_rows = [
        {
            "From Stage":      s_from,
            "To Stage":        s_to,
            "Demand Preview":  _demand_preview(s_to, s_from, _stage_props),
            "Demand Override": "",
        }
        for s_from, s_to in sorted(st.session_state.transitions)
    ]
    edited_demand = st.data_editor(
        pd.DataFrame(_demand_rows),
        use_container_width=True,
        num_rows="fixed",
        key="editor_demand",
        column_config={
            "From Stage":      st.column_config.TextColumn(disabled=True),
            "To Stage":        st.column_config.TextColumn(disabled=True),
            "Demand Preview":  st.column_config.TextColumn(
                disabled=True, width="large",
                help="Auto-computed from Stage Properties. Updates when detectors change.",
            ),
            "Demand Override": st.column_config.TextColumn(
                width="large",
                help="Leave blank = use auto. Fill to replace the demand for this transition.",
            ),
        },
    )

    st.divider()

    # ── Compile ───────────────────────────────────────────────────────────────
    if st.button("🔧 Compile JNET Logic", type="primary", use_container_width=True):

        # Validate: block if any "Check Manually" cells remain in the compact table
        bad = edited_to_rest[
            edited_to_rest["Rest of Skeleton"].str.strip().str.lower() == "check manually"
        ]
        if not bad.empty:
            st.error(
                f"**{len(bad)} target stage(s) still say 'Check Manually'** — "
                "correct them before compiling:"
            )
            st.dataframe(bad, use_container_width=True)
            st.stop()

        # Build override map: (From Stage, To Stage) → override string
        _override_map = {
            (row["From Stage"], row["To Stage"]): str(row["Demand Override"]).strip()
            for _, row in edited_demand.iterrows()
            if str(row["Demand Override"]).strip()
        }

        # Merge Rest of Skeleton + Demand Override into the final routes table
        edited_routes = pd.DataFrame([
            {
                "From Stage":      s_from,
                "To Stage":        s_to,
                "Rest of Skeleton": _to_rest_map.get(s_to, "Check Manually"),
                "Demand Override":  _override_map.get((s_from, s_to), ""),
            }
            for s_from, s_to in sorted(st.session_state.transitions)
        ])

        try:
            # Build in-memory config Excel → feed directly to engine
            config_bytes = build_config_excel(
                st.session_state.v_anchor,
                st.session_state.lrt_anchor,
                st.session_state.final_skel,
                edited_routes,
                edited_props,
            )
            cfg = parse_excel(io.BytesIO(config_bytes))

            with st.spinner("Compiling…"):
                logic_rows = compile_junction(cfg)

            df_logic = pd.DataFrame(logic_rows)
            st.subheader(f"✅ JNET Logic — {len(df_logic)} transitions")
            st.dataframe(df_logic, use_container_width=True)

            # Error summary
            error_rows = [r for r in logic_rows if str(r['JNET Logic Code']).startswith('ERROR')]
            if error_rows:
                st.warning(f"{len(error_rows)} row(s) produced errors:")
                for r in error_rows:
                    st.code(f"Row {r['#']} ({r['From']}→{r['To']}): {r['JNET Logic Code']}")

            # Build 4-sheet output Excel
            out_bytes = build_output_excel(
                st.session_state.v_anchor,
                st.session_state.lrt_anchor,
                st.session_state.final_skel,
                edited_routes,
                edited_props,
                logic_rows,
            )
            fname = f"{st.session_state.source_name}_JNET.xlsx"

            st.download_button(
                "⬇️ Download Excel (4 sheets — includes JNET Logic)",
                data=out_bytes,
                file_name=fname,
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                type="primary",
                use_container_width=True,
            )

        except ValueError as ve:
            st.error(f"Topology error: {ve}")
        except Exception as exc:
            st.error(f"Unexpected error: {exc}")
            with st.expander("Full traceback"):
                st.exception(exc)
