"""
templates.py — One function per template (A–G).
Edit this file to change the structure/conditions of any template.

Formatting note: all outputs are single-line strings (no embedded newlines)
to match the reference CSV format exactly.
"""

from __future__ import annotations


# ── Template A — Vehicle → Vehicle ────────────────────────────────────────────

def template_a(
    current: str,
    gt_func: str,
    demand: str,
    at_target: str,     # target with suffix (middle in AT), e.g. 'A1min' or 'Bcpn'
    at_jl: str,         # nearest LRT jl, e.g. 'jL39'
    bypass_wtg: str,    # AT_less bypass WTG content after current_, e.g. 'L30_DQ_Bcpn_Ccpn_A0' or 'L39'
    force_wtg: str,     # force-move WTG content after current_, e.g. 'A1min_A0' or 'A0'
    has_outgoing_lrt: bool = True,
) -> str:
    """
    Template A: Vehicle → Vehicle.
    A1 (standard): target has outgoing LRT.
    A2: target has no outgoing LRT — use threat LRT.
    """
    if has_outgoing_lrt:
        at_greater = f"AT_greater(1, ge, {current}_{at_target}_{at_jl}) and EG_{current}=true"
        at_less    = f"AT_less(1, ls, {current}_{at_target}_{at_jl}) and WTG({current}_{bypass_wtg})=false"
    else:
        at_greater = f"EG_{current}=true and AT_greater(1, gt, {current}_{at_target}_{at_jl})"
        at_less    = f"AT_less(1, ls, {current}_{at_target}_{at_jl}) and WTG({current}_{bypass_wtg})=false"

    core = (
        f"(PL=0 and EG_{current}=true) "
        f"or (PL>0 and GT({current}) >= {gt_func} and "
        f"(({at_greater}) or ({at_less}))) "
        f"or WTG({current}_{force_wtg})=false"
    )

    if demand:
        return f"{demand} and ({core})"
    return core


# ── Template B — Vehicle → LRT Entry (non-Anchor LRT) ────────────────────────

def template_b(
    current: str,
    lrt_target: str,    # e.g. 'L30'
    gt_func: str,
    wtg_rest: str,      # WTG content after lrt_target: 'DQ_Bcpn_Ccpn_A0'
    jlrt_target: str,   # e.g. 'jL30'
    nv_at_path: str,    # next_vehicle_suffixed_jNextLRT, e.g. 'Bcpn_jL31'
) -> str:
    """Template B: Vehicle → LRT Entry."""
    return (
        f"WTG({current}_{lrt_target}_{wtg_rest})=true "
        f"and ((GT({current}) >= {gt_func} and AT_less(0, le, {current}_{jlrt_target})) "
        f"or (EG_{current}=true and AT_less(0, le, {current}_{nv_at_path})))"
    )


# ── Template C — Vehicle → LRT Anchor ─────────────────────────────────────────

def template_c(
    current: str,
    lrt_anchor: str,    # e.g. 'L39'
    gt_func: str,
    j_lrt_anchor: str,  # e.g. 'jL39'
    va_at: str,         # vehicle_anchor_suffixed_jNearestLRTfromVA, e.g. 'A0min_jL30'
) -> str:
    """
    Template C: Vehicle → LRT Anchor.
    Two OR arms:
      Forced  — WTG does NOT schedule L39, but must enter it (ProgSwitch/Ghost/timing).
      Scheduled — WTG schedules L39 normally.
    """
    forced = (
        f"WTG({current}_{lrt_anchor})=false and "
        f"(ProgSwitch=true or Ghost=true or AT_less(0, le, {current}_{va_at}))"
    )
    scheduled = (
        f"WTG({current}_{lrt_anchor})=true and "
        f"(GT({current}) >= {gt_func} and AT_less(0, le, {current}_{j_lrt_anchor}))"
    )
    return f"({forced}) or ({scheduled})"


# ── Template D — LRT → Vehicle ────────────────────────────────────────────────

def template_d(
    current: str,
    target: str,        # bare name for CloseL/LIG checks
    at_path: str,       # 'target_suffixed_jlNext', e.g. 'Bcpn_jL31' or 'A0min_jL30'
    wtg_path: str,      # path after DQ: 'Bcpn_Ccpn_A0' or 'A0' (bare if last)
    demand: str,
) -> str:
    """
    Template D: LRT → Vehicle.
    No GT check (CloseL guarantees minimum). No EG in AT_greater.
    DQ immediately after Current in WTG.
    """
    close = f"CloseL({target}) and LIG({target})=false"
    inner = f"(AT_greater(1, ge, {current}_{at_path}) or WTG({current}_DQ_{wtg_path})=false)"
    if demand:
        return f"{close} and {demand} and {inner}"
    return f"{close} and {inner}"


# ── Template E — LRT → Lig Stage ──────────────────────────────────────────────

def template_e(
    current: str,
    lig: str,           # Lig stage bare name, e.g. 'A30'
    gt_func: str,
    at_path: str,       # 'Lig_NVsuffixed_jNextLRT', e.g. 'A30_Bcpn_jL31'
    wtg_path: str,      # path after DQ: 'Lig_NVsuffixed_..._Anchor', e.g. 'A30_Bcpn_Ccpn_A0'
) -> str:
    """
    Template E: LRT → Lig Stage.
    AT_greater: includes Lig (bare) + NextVehicle (suffixed) + jNextLRT.
    WTG: DQ first, then Lig (bare) + vehicle stages to Anchor.
    """
    return (
        f"CloseL({lig}) and LIG({lig})=true and "
        f"((GT({current}) >= {gt_func} and AT_greater(1, ge, {current}_{at_path})) "
        f"or WTG({current}_DQ_{wtg_path})=false)"
    )


# ── Template F — Lig Stage → Vehicle ──────────────────────────────────────────

def template_f(demand: str) -> str:
    """Template F: Lig → Vehicle. Logic = demand string only, or NO_LOGIC."""
    return demand if demand else "NO_LOGIC"


# ── Template G — LRT → LRT Chaining ───────────────────────────────────────────

def template_g(
    current: str,
    lrt_target: str,            # e.g. 'L39'
    jlrt_target: str,           # e.g. 'jL39'
    nv_at_path: str,            # vehicle_after_lrt_target_suffixed_jlrtTarget, e.g. 'A0min_jL39'
    demand: str = '',
) -> str:
    """
    Template G: LRT → LRT chaining.
    No DQ (LRT → LRT, no vehicle entry).
    nv_at_path uses the vehicle after the TARGET LRT (not the current LRT).
    """
    core = (
        f"(EG_{current}=true and AT_less(0, le, {current}_{jlrt_target})) "
        f"or (WTG({current}_{lrt_target})=true and AT_less(0, ls, {current}_{nv_at_path}))"
    )
    if demand:
        return f"{demand} and ({core})"
    return core
