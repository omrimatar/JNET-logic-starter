"""
compiler.py — Main orchestrator: iterates transitions, calls the right template.

Key convention:
  rest_stages[0]  = To stage (the target itself)
  rest_stages[1:] = tail — stages from target onwards to anchor

This means we ALWAYS strip rest_stages[0] (= to_s) before building WTG/AT paths.
"""

from __future__ import annotations
from engine.parser import JunctionConfig, Transition
from engine.config import get_template, is_lrt, is_lig
from engine.topology import (
    build_graph,
    find_outgoing_lrts,
    find_nearest_lrt_from_stage,
    lrt_to_j,
    apply_suffix,
    parse_rest_of_skeleton,
    validate_topology,
)
from engine.demand import build_demand
from engine.templates import (
    template_a, template_b, template_c,
    template_d, template_e, template_f, template_g,
)


# ── Helpers ────────────────────────────────────────────────────────────────────

def _gt_func(stage: str, stage_props: dict) -> str:
    props = stage_props.get(stage)
    if props and props.min_type == 'cpn':
        return f"GTcpmin({stage})"
    return f"GTmin_{stage}"


def _tail_str(tail_stages: list[str], stage_props: dict) -> str:
    """
    Convert a tail list (stages after target, up to anchor) to a suffix string.
    Rules: last element bare, all others suffixed.
    Special: LRT stages, Lig stages, 'DQ' are always bare even if middle.
    """
    if not tail_stages:
        return ''
    if len(tail_stages) == 1:
        return tail_stages[0]   # last → bare

    result = []
    for i, s in enumerate(tail_stages):
        is_last = (i == len(tail_stages) - 1)
        if is_last or is_lrt(s) or is_lig(s) or s == 'DQ':
            result.append(s)
        else:
            result.append(apply_suffix(s, stage_props))
    return '_'.join(result)


def _find_lrt_current(from_s: str, graph: dict, lrt_anchor: str) -> str | None:
    """
    Find the LRT stage directly reachable from from_s to use in the AT_less bypass WTG.
    Preference: non-anchor LRT entries over the LRT Anchor.
    """
    lrts = find_outgoing_lrts(from_s, graph)
    if not lrts:
        return None
    # Prefer non-anchor first
    non_anchor = [l for l in lrts if l != lrt_anchor]
    return non_anchor[0] if non_anchor else lrts[0]



def _find_next_vehicle_in_skeleton(lrt_stage: str, graph: dict,
                                    cfg: JunctionConfig) -> str:
    """
    For Template G: find the first vehicle stage that follows lrt_stage
    in the vehicle skeleton cycle (not just any graph neighbour).
    Uses skeleton_stages order to pick the correct successor.
    """
    sp = cfg.stage_props
    va = cfg.vehicle_anchor
    la = cfg.lrt_anchor
    skeleton = cfg.skeleton_stages  # e.g. ['A0', 'B', 'C', 'A0']

    # Get all vehicle neighbours of lrt_stage (excluding anchor and lig)
    vehicle_nbrs = [n for n in graph.get(lrt_stage, [])
                    if not is_lrt(n) and not is_lig(n) and n != va]

    if not vehicle_nbrs:
        return va  # fallback

    # Sort by skeleton position (stages earlier in skeleton come first)
    skeleton_unique = list(dict.fromkeys(skeleton))  # deduplicated, ordered
    def skeleton_pos(s: str) -> int:
        try:
            return skeleton_unique.index(s)
        except ValueError:
            return 999  # not in skeleton → sort last

    vehicle_nbrs.sort(key=skeleton_pos)
    return vehicle_nbrs[0]


# ── Main compile function ──────────────────────────────────────────────────────

def compile_junction(cfg: JunctionConfig) -> list[dict]:
    validate_topology(cfg.transitions, cfg.vehicle_anchor)
    graph = build_graph(cfg.transitions)

    rows = []
    for row_num, trans in enumerate(cfg.transitions, start=2):
        from_s = trans.from_stage
        to_s   = trans.to_stage

        template_letter = get_template(from_s, to_s, cfg.lrt_anchor)

        rest_stages = parse_rest_of_skeleton(
            trans.rest_of_skeleton, cfg.stage_props,
            cfg.vehicle_anchor, cfg.lrt_anchor
        )
        # tail = everything after the target (to_s)
        tail_stages = rest_stages[1:] if rest_stages else []

        try:
            code = _dispatch(template_letter, from_s, to_s,
                              tail_stages, graph, cfg,
                              demand_override=trans.demand_override)
        except Exception as e:
            import traceback
            code = f"ERROR: {e} | {traceback.format_exc()}"

        rows.append({
            '#': row_num,
            'From': from_s,
            'To': to_s,
            'Template': template_letter,
            'JNET Logic Code': code,
        })

    return rows


# ── Template dispatcher ────────────────────────────────────────────────────────

def _dispatch(template: str, from_s: str, to_s: str,
              tail_stages: list[str],
              graph: dict, cfg: JunctionConfig,
              demand_override: str = '') -> str:

    sp = cfg.stage_props
    va = cfg.vehicle_anchor
    la = cfg.lrt_anchor

    gt     = _gt_func(from_s, sp)
    demand = demand_override.strip() if demand_override.strip() else build_demand(to_s, from_s, sp)

    # ── ProgSwitch / Ghost — junction-wide LRT flags ───────────────────────────
    # Entering the vehicle anchor requires LRT to be inactive.
    # Entering the LRT anchor (Template G) requires LRT to be active.
    # Template C handles ProgSwitch/Ghost inside its own WTG=false arm.
    if to_s == va:
        pg = "ProgSwitch=false and Ghost=false"
        demand = f"{pg} and {demand}" if demand else pg
    elif to_s == la:
        pg = "(ProgSwitch=true or Ghost=true)"
        demand = f"{pg} and {demand}" if demand else pg

    # ── Template F ─────────────────────────────────────────────────────────────
    if template == 'F':
        return template_f(demand)

    # ── Template A — Vehicle → Vehicle ────────────────────────────────────────
    if template == 'A':
        # target is always a MIDDLE element in AT strings (jLXX follows)
        at_target = apply_suffix(to_s, sp)

        # nearest LRT from Target for AT conditions
        nearest_lrt = find_nearest_lrt_from_stage(to_s, graph, la)
        has_outgoing = bool(find_outgoing_lrts(to_s, graph))

        if nearest_lrt is None:
            # No LRT reachable from target → Variant A2: use nearest from Current
            nearest_lrt = find_nearest_lrt_from_stage(from_s, graph, la)
            has_outgoing = False

        jl_nearest = lrt_to_j(nearest_lrt) if nearest_lrt else 'jL_UNKNOWN'

        # Force-move WTG path (after current_)
        tail_s = _tail_str(tail_stages, sp)
        if tail_stages:
            force = f"{at_target}_{tail_s}"
        else:
            force = to_s  # target is LAST → bare, no suffix

        # AT_less bypass WTG: via LRT reachable from Current, then DQ, then
        # the same target path as force_wtg (NOT the LRT's own rest-of-skeleton).
        # LRT anchor has no DQ; non-anchor entries get DQ inserted.
        lrt_current = _find_lrt_current(from_s, graph, la)
        if lrt_current is None:
            lrt_current = nearest_lrt or la
        if lrt_current == la:
            bypass = la
        else:
            bypass = f"{lrt_current}_DQ_{force}"

        return template_a(
            current         = from_s,
            gt_func         = gt,
            demand          = demand,
            at_target       = at_target,
            at_jl           = jl_nearest,
            bypass_wtg      = bypass,
            force_wtg       = force,
            has_outgoing_lrt= has_outgoing,
        )

    # ── Template B — Vehicle → LRT Entry ──────────────────────────────────────
    if template == 'B':
        # tail_stages here: everything after the LRT target (to_s already stripped)
        # Next vehicle = first element of tail_stages
        next_vehicle = tail_stages[0] if tail_stages else va
        nv_suffixed  = apply_suffix(next_vehicle, sp)

        # j_next_lrt: LRT threatening next_vehicle
        lrts_from_nv = find_outgoing_lrts(next_vehicle, graph)
        j_next_lrt   = lrt_to_j(lrts_from_nv[0]) if lrts_from_nv else lrt_to_j(la)

        # WTG rest: DQ + tail
        tail_s  = _tail_str(tail_stages, sp)
        wtg_rest = f"DQ_{tail_s}" if tail_s else f"DQ_{va}"

        # Second AT: current_nv_suffixed_jNextLRT
        nv_at_path = f"{nv_suffixed}_{j_next_lrt}"

        return template_b(
            current     = from_s,
            lrt_target  = to_s,
            gt_func     = gt,
            wtg_rest    = wtg_rest,
            jlrt_target = lrt_to_j(to_s),
            nv_at_path  = nv_at_path,
        )

    # ── Template C — Vehicle → LRT Anchor ─────────────────────────────────────
    if template == 'C':
        # va_at: vehicle anchor (suffixed, middle element) + nearest LRT from va
        # Used in the WTG=false arm: AT_less(0, le, current_va_at)
        nearest_lrt_va = find_nearest_lrt_from_stage(va, graph, la)
        j_lrt_va = lrt_to_j(nearest_lrt_va) if nearest_lrt_va else lrt_to_j(la)
        va_at = f"{apply_suffix(va, sp)}_{j_lrt_va}"

        return template_c(
            current      = from_s,
            lrt_anchor   = la,
            gt_func      = gt,
            j_lrt_anchor = lrt_to_j(la),
            va_at        = va_at,
        )

    # ── Template D — LRT → Vehicle ────────────────────────────────────────────
    if template == 'D':
        # nearest LRT from Target for AT_greater
        nearest_lrt = find_nearest_lrt_from_stage(to_s, graph, la)
        if nearest_lrt is None:
            nearest_lrt = la
        jl_next = lrt_to_j(nearest_lrt)

        # AT path: target_suffixed_jlNext (target is middle in AT — jl follows)
        at_target_s = apply_suffix(to_s, sp)
        at_path     = f"{at_target_s}_{jl_next}"

        # WTG path after DQ:
        #   - tail empty (end of skeleton): target is LAST → bare
        #   - tail non-empty: target is MIDDLE → suffixed, then tail
        tail_s = _tail_str(tail_stages, sp)
        if tail_stages:
            wtg_path = f"{at_target_s}_{tail_s}"
        else:
            wtg_path = to_s   # bare — last element

        return template_d(
            current  = from_s,
            target   = to_s,
            at_path  = at_path,
            wtg_path = wtg_path,
            demand   = demand,
        )

    # ── Template E — LRT → Lig Stage ──────────────────────────────────────────
    if template == 'E':
        # tail_stages here: everything after the Lig target (to_s stripped)
        # e.g. for L30→A30, rest was ['A30','B','C','A0'] → tail = ['B','C','A0']
        next_vehicle = tail_stages[0] if tail_stages else va
        nv_suffixed  = apply_suffix(next_vehicle, sp)

        # j_next_lrt: LRT threatening next_vehicle
        lrts_from_nv = find_outgoing_lrts(next_vehicle, graph)
        j_next_lrt   = lrt_to_j(lrts_from_nv[0]) if lrts_from_nv else lrt_to_j(la)

        # AT path: Lig(bare) _ NV(suffixed) _ jNextLRT
        at_path = f"{to_s}_{nv_suffixed}_{j_next_lrt}"

        # WTG path after DQ: Lig(bare) _ tail_suffix
        # tail_stages starts with next_vehicle, e.g. ['B','C','A0']
        tail_s  = _tail_str(tail_stages, sp)
        wtg_path = f"{to_s}_{tail_s}" if tail_s else f"{to_s}_{va}"

        return template_e(
            current  = from_s,
            lig      = to_s,
            gt_func  = gt,
            at_path  = at_path,
            wtg_path = wtg_path,
        )

    # ── Template G — LRT → LRT Chaining ───────────────────────────────────────
    if template == 'G':
        jlrt_target  = lrt_to_j(to_s)
        # Use the vehicle that follows the TARGET LRT (to_s), not the current LRT (from_s),
        # because the WTG/AT_less condition checks feasibility of the target LRT's window.
        next_vehicle = _find_next_vehicle_in_skeleton(to_s, graph, cfg)
        nv_suffixed  = apply_suffix(next_vehicle, sp)
        nv_at_path   = f"{nv_suffixed}_{jlrt_target}"

        return template_g(
            current      = from_s,
            lrt_target   = to_s,
            jlrt_target  = jlrt_target,
            nv_at_path   = nv_at_path,
            demand       = demand,
        )

    raise ValueError(f"Unknown template letter: {template}")
