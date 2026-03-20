"""
parser.py — Reads the Excel skeleton file and returns normalised data structures.
"""

from __future__ import annotations
from dataclasses import dataclass, field
import pandas as pd


@dataclass
class StageProps:
    name: str
    min_type: str          # 'cpn' or 'min'
    detector: str          # raw detector string, or '' if none
    waterfall_level: float | None
    sibling_priority: float | None


@dataclass
class Transition:
    from_stage: str
    to_stage: str
    rest_of_skeleton: str   # raw string from file, e.g. 'B-C-A0' or 'end of skeleton'
    demand_override: str = ''  # if non-empty, replaces auto-computed demand


@dataclass
class JunctionConfig:
    vehicle_anchor: str
    lrt_anchor: str
    max_skeleton: str                         # e.g. 'A0 - B - C - A0'
    skeleton_stages: list[str]                # ordered vehicle stages (parsed from max_skeleton)
    transitions: list[Transition]
    stage_props: dict[str, StageProps]        # keyed by stage name


def _parse_skeleton_stages(skeleton_str: str) -> list[str]:
    """'A0 - B - C - A0'  →  ['A0', 'B', 'C', 'A0']"""
    parts = [p.strip() for p in skeleton_str.replace('→', '-').split('-')]
    return [p for p in parts if p]


def _safe_str(val) -> str:
    if pd.isna(val):
        return ''
    return str(val).strip()


def _safe_float(val) -> float | None:
    try:
        f = float(val)
        return None if pd.isna(f) else f
    except (TypeError, ValueError):
        return None


def parse_excel(file) -> JunctionConfig:
    xl = pd.ExcelFile(file)

    # ── General Info ──────────────────────────────────────────────────────────
    gi = xl.parse('General Info')
    gi_map: dict[str, str] = {}
    for _, row in gi.iterrows():
        vals = [_safe_str(v) for v in row if _safe_str(v)]
        if len(vals) >= 2:
            gi_map[vals[0]] = vals[1]

    vehicle_anchor = gi_map.get('Vehicle Anchor', '')
    lrt_anchor     = gi_map.get('LRT Anchor', '')
    max_skeleton   = gi_map.get('Maximum Skeleton', '')
    skeleton_stages = _parse_skeleton_stages(max_skeleton)

    # ── Stages Properties ─────────────────────────────────────────────────────
    sp_df = xl.parse('Stages Properties').dropna(how='all')
    stage_props: dict[str, StageProps] = {}

    for _, row in sp_df.iterrows():
        name = _safe_str(row.get('Stage', ''))
        if not name:
            continue
        props = StageProps(
            name             = name,
            min_type         = _safe_str(row.get('Minimum Type', 'min')) or 'min',
            detector         = _safe_str(row.get('Detectors', '')),
            waterfall_level  = _safe_float(row.get('Waterfall Level')),
            sibling_priority = _safe_float(row.get('Sibling Priority')),
        )
        stage_props[name] = props

    # ── Inter-Stages ──────────────────────────────────────────────────────────
    is_df = xl.parse('Inter-Stages').dropna(how='all')
    transitions: list[Transition] = []

    for _, row in is_df.iterrows():
        from_s   = _safe_str(row.get('From Stage', ''))
        to_s     = _safe_str(row.get('To Stage', ''))
        rest     = _safe_str(row.get('Rest of Skeleton', ''))
        override = _safe_str(row.get('Demand Override', ''))
        if from_s and to_s:
            transitions.append(Transition(from_s, to_s, rest, override))

    return JunctionConfig(
        vehicle_anchor  = vehicle_anchor,
        lrt_anchor      = lrt_anchor,
        max_skeleton    = max_skeleton,
        skeleton_stages = skeleton_stages,
        transitions     = transitions,
        stage_props     = stage_props,
    )
