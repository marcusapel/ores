#!/usr/bin/env python3
"""
WeCo Demo Runner — Interactive GUI
====================================
PyQt6 application for running WeCo example datasets interactively.
Allows selecting datasets, tweaking parameters, running correlations,
and viewing result plots inline.

Usage:
    source ~/.venv/bin/activate
    python demo_gui.py
"""

import sys
import os
import io
from pathlib import Path
from contextlib import redirect_stdout, redirect_stderr

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import numpy as np

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QSplitter, QTreeWidget, QTreeWidgetItem, QLabel, QPushButton,
    QTextEdit, QGroupBox, QFormLayout, QDoubleSpinBox, QSpinBox,
    QComboBox, QLineEdit, QTabWidget, QScrollArea, QCheckBox,
    QProgressBar, QFrame, QSizePolicy, QFileDialog, QColorDialog,
    QGridLayout, QListWidget, QListWidgetItem
)
from PyQt6.QtGui import QPixmap, QFont, QColor, QTextCursor, QIcon
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QSize, QTimer

from weco.ext import ProjectExt
from weco.data import WellList, ResFile
from weco.engine import get_version

# ═══════════════════════════════════════════════════════════════════════════
#  Dataset Definitions (same as auto_run_examples.py)
# ═══════════════════════════════════════════════════════════════════════════

SCRIPT_DIR = Path(__file__).resolve().parent.parent  # bin/ → project root
DATA_DIR = SCRIPT_DIR / "demo" / "data"
OUTPUT_DIR = SCRIPT_DIR / "tmp" / "img"

WELL_COLORS = plt.cm.tab10.colors
LOG_COLORS = ['#1565c0', '#c62828', '#2e7d32', '#6a1b9a', '#e65100']

# Full option reset dict to prevent global state leakage
RESET_OPTS = {
    "no_crossing": "", "no_crossing2": "", "no_crossing3": "",
    "same_region": "", "same_region2": "", "same_region3": "",
    "polarity_region": "", "var_region": "",
    "var_data": "", "var_data2": "", "var_data3": "",
    "var_data4": "", "var_data5": "",
    "var_weight": 1.0, "var_weight2": 1.0, "var_weight3": 1.0,
    "var_weight4": 1.0, "var_weight5": 1.0,
    "dist_distal": "", "dist_facies": "",
    "gap_cost_func": "", "const_gap_cost": 0.0,
    "const_gap_cost_start": -1.0, "const_gap_cost_end": -1.0,
    "multi_dist_distal": "", "multi_dist_facies": "",
    "min_dist": 0.1, "out_min_dist": 0.05,  # diversity always on
}

# Default AI feature settings per demo
AI_DEFAULTS = {
    "quality": True,       # CorrelationQuality scoring
    "anomaly": False,      # CorrelationAnomalyDetector
    "uncertainty": False,  # CorrelationUncertainty (requires nbr_cor > 1)
    "log_qc": False,       # LogQC preprocessing (washout, impute, normalise)
}

# Parameter help text (tooltips) — geological context for each engine option
PARAM_HELP = {
    "var_data": "Primary log curve for correlation cost (e.g. GR, DEN, NPHI).\nThe engine computes variance-based DTW cost from this curve.",
    "var_data2": "Second log curve (multi-log correlation). Combined with var_weight2.",
    "var_data3": "Third log curve. Useful for multi-attribute correlation.",
    "var_data4": "Fourth log curve (rarely needed).",
    "var_data5": "Fifth log curve (rarely needed).",
    "var_weight": "Weight [0–5] for primary log. Higher = more influence on cost.\n1.0 = standard. Use >1 for dominant logs (e.g. GR in clastic).",
    "var_weight2": "Weight for second log curve.",
    "var_weight3": "Weight for third log curve.",
    "var_weight4": "Weight for fourth log curve.",
    "var_weight5": "Weight for fifth log curve.",
    "const_gap_cost": "Penalty for introducing a gap (hiatus/non-deposition).\n0 = gaps are free → many hiatuses.\nHigher (3–8) = penalise gaps → more 1:1 matching.\nGeology: use low for fluvial/discontinuous, high for marine/layer-cake.",
    "band_width": "DTW band-width constraint (% of well length).\nLimits how far a correlation can deviate from diagonal.\n10–30 typical. Narrow = faster but may miss large thickness changes.",
    "min_dist": "Minimum distance between consecutive correlation points (fraction).\nPrevents over-correlating thin beds. 0.05–0.2 typical.",
    "out_min_dist": "Minimum distance for output correlation points.\nControls density of output markers. Usually ≤ min_dist.",
    "nbr_cor": "Number of correlation lines to compute internally.\nHigher = more detail but slower. 50–200 typical.",
    "out_nbr_cor": "Number of correlation lines to OUTPUT (k-best).\nk-best results capture geological uncertainty.\n5–20 typical for exploration; 1 for production.",
    "max_cor": "Maximum correlation lines allowed.\nSafety cap for memory. 100–500 typical.",
    "order": "Well ordering strategy for multi-well correlation.\n• linear: left-to-right as given\n• pyramidal: center-out (recommended for >4 wells)\n• position: by geographic X coordinate\n• distality: proximal→distal (requires distality data)\n• inverse: reverse of linear",
    "no_crossing": "Region name for no-crossing constraint.\nCorrelation lines cannot cross boundaries of this region.\nUseful for biozones, sequence boundaries, dated markers.",
    "no_crossing2": "Second no-crossing region (additional constraint).",
    "no_crossing3": "Third no-crossing region.",
    "same_region": "Region where correlated points must share the same label.\nForces correlation within same litho/bio unit.",
    "dist_distal": "Region name containing distality labels (e.g. DISTAL).\nUsed by Walther's Law cost — penalises correlating unlike facies belts.",
    "dist_facies": "Region name containing facies for distality cost.\nFacies identity determines the cost penalty across wells.",
    "dist_scaling": "Scaling factor for distality cost contribution.\n0 = disabled, 1.0 = full weight. Controls how strongly\nfacies mismatch is penalised.",
    "gap_cost_func": "Gap cost function shape: '' (constant), 'linear', 'sigmoid'.\nControls how gap penalty varies with gap size.",
    "cost_floor": "Minimum cost value (floor). Prevents zero-cost matches\nfrom dominating. Useful when log values are very similar.",
}

DATASETS = {
    # ── Concept Demos (real data, teaching distality/gap cost) ────────
    "1_distality": {
        "title": "Distality-Facies Cost",
        "subtitle": "2 real wells · palaeo-geographic cost",
        "description": (
            "Two wells (A, B) with DISTAL/FACIES_1 regions.\n"
            "Distality cost penalises correlating proximal facies\n"
            "with distal facies. Order = distality (most-distal first)."
        ),
        "wells": DATA_DIR / "data_set_distality" / "wells.txt",
        "runs": [
            {"name": "Distality (FACIES_1)", "opts": {
                "dist_distal": "DISTAL", "dist_facies": "FACIES_1", "dist_scaling": 1.0}},
        ],
        "common_opts": {"cost_function": "composite", "order": "distality",
                        "max_cor": 200, "nbr_cor": 100, "out_nbr_cor": 20,
                        "min_dist": 0.1, "out_min_dist": 0.05},
        "ai": {"quality": True, "anomaly": False, "uncertainty": False, "log_qc": False},
    },
    "2_gap_cost": {
        "title": "Gap Cost Exploration",
        "subtitle": "2 real wells · varying gap penalty",
        "description": (
            "Explores the effect of const-gap-cost which penalises gaps.\n"
            "Higher gap cost → more 1-to-1 matching; lower → allows hiatuses."
        ),
        "wells": DATA_DIR / "data_set_biozone_distality" / "wells.txt",
        "runs": [
            {"name": "Gap cost = 0", "opts": {"const_gap_cost": 0.0,
                                              "dist_distal": "DISTAL", "dist_facies": "FACIES_1"}},
            {"name": "Gap cost = 5", "opts": {"const_gap_cost": 5.0,
                                              "dist_distal": "DISTAL", "dist_facies": "FACIES_1"}},
            {"name": "Gap cost = 8", "opts": {"const_gap_cost": 8.0,
                                              "dist_distal": "DISTAL", "dist_facies": "FACIES_1"}},
        ],
        "common_opts": {"cost_function": "composite", "order": "distality",
                        "max_cor": 200, "nbr_cor": 100, "out_nbr_cor": 20,
                        "min_dist": 0.1, "out_min_dist": 0.05},
        "ai": {"quality": True, "anomaly": False, "uncertainty": False, "log_qc": False},
    },
    # ── Domain Demos ──────────────────────────────────────────────────
    "3_coal_basin": {
        "title": "Coal Basin – Gap Cost + Multi-Log (DEN+GR)",
        "subtitle": "10 coal boreholes · 6 named seams",
        "description": (
            "Correlate coal SEAM boundaries across the basin.\n"
            "Red lines = seam contacts (sharp DEN transitions at 1.3→2.5 g/cc).\n"
            "Blue gaps = seam splitting/merging or washout erosion.\n"
            "The k-best results capture uncertainty in seam continuity —\n"
            "crucial for resource estimation and mine planning."
        ),
        "geology_note": (
            "Setting: Intracratonic coal basin (Carboniferous, Ruhr/Upper Silesian analogue).\n"
            "Cyclothem model (roof shale → marine band → COAL → tonstein → seat earth → sandstone → siltstone).\n"
            "6 named seams: Katharina (3.0 m, 100% persistent), Sonnenschein (1.5 m, marine band above),\n"
            "Präsident (2.5 m, frequent splitting), Zollverein (1.8 m), Flöz 9 (1.2 m), Flöz 10 (0.8 m).\n"
            "Key features: seam splitting, washout zones (fluvial erosion), tonstein isochronous markers,\n"
            "marine bands (Goniatitenschicht), brandschiefer (burnt shale), ironstone nodules.\n"
            "References: Diessel (1992), Thomas (2002), Ward (2016)."
        ),
        "wells": DATA_DIR / "data_set_coal" / "wells_10.txt",
        "runs": [
            {"name": "DEN+GR (standard)", "opts": {
                "var_data": "DEN", "var_weight": 0.6,
                "var_data2": "GR", "var_weight2": 0.4,
                "const_gap_cost": 3.0}},
            {"name": "DEN only", "opts": {
                "var_data": "DEN", "var_weight": 1.0,
                "const_gap_cost": 3.0}},
            {"name": "Multi-log (5)", "opts": {
                "var_data": "GR", "var_weight": 0.25,
                "var_data2": "DEN", "var_weight2": 0.35,
                "var_data3": "RT", "var_weight3": 0.15,
                "var_data4": "SON", "var_weight4": 0.15,
                "var_data5": "NEU", "var_weight5": 0.10,
                "const_gap_cost": 3.0}},
        ],
        "common_opts": {"cost_function": "composite",
                        "max_cor": 200, "nbr_cor": 100, "out_nbr_cor": 20,
                        "min_dist": 0.1, "out_min_dist": 0.05,
                        "band_width": 15},
        "ai": {"quality": True, "anomaly": True, "uncertainty": True, "log_qc": True},
    },
    "4_quaternary": {
        "title": "Quaternary – Gap Cost + Multi-Log (GR+RT)",
        "subtitle": "20 shallow wells · glacial lowland",
        "description": (
            "Correlate aquifer/aquitard BOUNDARIES (sand↔till contacts).\n"
            "Red lines = lithological contacts (GR transitions at aquifer tops/bases).\n"
            "Blue gaps = glacial erosion surfaces or unit pinch-out.\n"
            "Key for groundwater model layering (which aquifers connect)."
        ),
        "geology_note": (
            "Setting: Northern European glacial lowland (Pleistocene).\n"
            "5 lithostratigraphic units: Holocene cover → Weichselian till/outwash →\n"
            "Eemian interglacial (clay/peat, often missing) → Saalian till/outwash → Elsterian tunnel-valley fill.\n"
            "6 facies: gravel, sand, silty sand, till, clay, peat + periglacial features\n"
            "(ice-wedge casts, cryoturbation, dropstones).\n"
            "Logs: GR (lithology), RT (permeability), SPT (geotechnical), COND (hydraulic conductivity),\n"
            "MS (magnetic susceptibility — till indicator), WC (water content).\n"
            "References: Ehlers & Gibbard (2004), Keys (1990), Vandenberghe (2003)."
        ),
        "wells": DATA_DIR / "data_set_quaternary" / "wells_20.txt",
        "runs": [
            {"name": "GR+RT (standard)", "opts": {
                "var_data": "GR", "var_weight": 0.7,
                "var_data2": "RT", "var_weight2": 0.3,
                "const_gap_cost": 1.5}},
            {"name": "GR+RT+SPT (3-log)", "opts": {
                "var_data": "GR", "var_weight": 0.50,
                "var_data2": "RT", "var_weight2": 0.25,
                "var_data3": "SPT", "var_weight3": 0.25,
                "const_gap_cost": 2.0}},
        ],
        "common_opts": {"cost_function": "composite",
                        "max_cor": 200, "nbr_cor": 100, "out_nbr_cor": 20,
                        "min_dist": 0.1, "out_min_dist": 0.05,
                        "band_width": 20},
        "ai": {"quality": True, "anomaly": True, "uncertainty": True, "log_qc": True},
    },
    "5_shallow_marine": {
        "title": "Shallow Marine – 3-Log Variance (GR+RHOB+DT) + Gap Cost",
        "subtitle": "10 wells · wave-dominated shoreface",
        "description": (
            "Correlate FLOODING SURFACES (parasequence boundaries).\n"
            "Red lines = maximum flooding surfaces (GR spikes at shale drapes).\n"
            "Blue gaps = condensation or bypass — thinner downdip sections.\n"
            "BIOZONE no-crossing locks bio-datum planes for Wheeler diagram."
        ),
        "geology_note": (
            "Setting: Hugin Formation analogue (Upper Jurassic, North Sea).\n"
            "Wave-dominated shoreface / bay-fill system, 10 wells along depositional dip.\n"
            "5 parasequences (PS1–PS5) with clinoform geometry and lateral facies change.\n"
            "8 facies: offshore mud, offshore transition, lower/upper shoreface, foreshore,\n"
            "bay-fill mud, tidal channel, transgressive lag.\n"
            "Biozones BZ1 (base PS2) and BZ2 (base PS4) serve as no-crossing constraints.\n"
            "References: Baville (2022), Kieft et al. (2010), Catuneanu (2006)."
        ),
        "wells": DATA_DIR / "data_set_shallow_marine" / "wells.txt",
        "runs": [
            {"name": "GR+RHOB+DT", "opts": {
                "var_data": "GR", "var_weight": 0.5,
                "var_data2": "RHOB", "var_weight2": 0.3,
                "var_data3": "DT", "var_weight3": 0.2,
                "const_gap_cost": 2.0}},
            {"name": "With BIOZONE no-crossing", "opts": {
                "var_data": "GR", "var_weight": 0.5,
                "var_data2": "RHOB", "var_weight2": 0.3,
                "var_data3": "DT", "var_weight3": 0.2,
                "no_crossing": "BIOZONE",
                "const_gap_cost": 2.0}},
        ],
        "common_opts": {"cost_function": "composite",
                        "max_cor": 200, "nbr_cor": 100, "out_nbr_cor": 20,
                        "min_dist": 0.1, "out_min_dist": 0.05,
                        "band_width": 20},
        "ai": {"quality": True, "anomaly": True, "uncertainty": True, "log_qc": False},
    },
    "6_fluvial": {
        "title": "Fluvial – Gap Cost + High Diversity",
        "subtitle": "12 wells · laterally discontinuous channels",
        "description": (
            "Correlate CHANNEL BASE/TOP contacts (sand↔floodplain transitions).\n"
            "Blue gaps dominate — channels pinch out laterally by definition.\n"
            "Low gap-cost allows hiatuses (not every channel reaches every well).\n"
            "High gap-cost forces layer-cake (wrong for fluvial architecture)."
        ),
        "geology_note": (
            "Setting: Meandering/braided fluvial system.\n"
            "6 facies: floodplain (GR~120), crevasse splay (GR~75), channel fill (GR~30),\n"
            "channel lag (GR~15), levee (GR~90), oxbow lake (GR~110).\n"
            "Channels meander sinusoidally and pinch out laterally — this is one of the\n"
            "hardest correlation scenarios because sand bodies are inherently discontinuous.\n"
            "The gap cost parameter controls the balance between layer-cake (wrong) and\n"
            "event-based (correct) interpretations.\n"
            "References: Bridge (2003), Miall (2014)."
        ),
        "wells": DATA_DIR / "data_set_fluvial" / "wells.txt",
        "runs": [
            {"name": "GR + gap cost", "opts": {
                "var_data": "GR", "var_weight": 1.0,
                "const_gap_cost": 0.5}},
            {"name": "GR no gap cost", "opts": {
                "var_data": "GR", "var_weight": 1.0,
                "const_gap_cost": 0.0}},
        ],
        "common_opts": {"cost_function": "composite",
                        "max_cor": 200, "nbr_cor": 100, "out_nbr_cor": 20,
                        "min_dist": 0.15, "out_min_dist": 0.05,
                        "band_width": 20},
        "ai": {"quality": True, "anomaly": True, "uncertainty": True, "log_qc": False},
    },
    "7_delta": {
        "title": "Delta – Multi-Log Variance (GR+DEN) + Sequence Boundaries",
        "subtitle": "8 wells · shingled parasequences · SEQSTRAT no-crossing",
        "description": (
            "Correlate CLINOFORM SURFACES (parasequence boundaries).\n"
            "no_crossing=SEQSTRAT locks the high-order sequence boundaries\n"
            "(the most geologically important surfaces) so correlations\n"
            "are distributed throughout the section — not just at the top.\n"
            "k-best results show alternative clinoform geometries."
        ),
        "geology_note": (
            "Setting: Prograding river-dominated delta.\n"
            "8 facies: prodelta shale, distal/proximal delta front, distributary mouth bar,\n"
            "distributary channel, interdistributary bay, marsh, delta plain.\n"
            "6 parasequences with coarsening-upward profiles; beds thicken and coarsen\n"
            "landward as the delta progrades basinward.\n"
            "Wells ordered by distality — proximal wells see more sand, distal more shale.\n"
            "References: Bhattacharya (2006), Olariu & Bhattacharya (2006)."
        ),
        "wells": DATA_DIR / "data_set_delta" / "wells.txt",
        "runs": [
            {"name": "GR+DEN_seqstrat", "opts": {
                "var_data": "GR", "var_weight": 0.6,
                "var_data2": "DEN", "var_weight2": 0.4,
                "no_crossing": "SEQSTRAT",
                "const_gap_cost": 1.0}},
            {"name": "GR+DEN_unconstrained", "opts": {
                "var_data": "GR", "var_weight": 0.6,
                "var_data2": "DEN", "var_weight2": 0.4,
                "const_gap_cost": 2.0}},
        ],
        "common_opts": {"cost_function": "composite",
                        "max_cor": 200, "nbr_cor": 100, "out_nbr_cor": 20,
                        "min_dist": 0.1, "out_min_dist": 0.05,
                        "band_width": 20},
        "ai": {"quality": True, "anomaly": False, "uncertainty": True, "log_qc": False},
    },
    "8_bryson": {
        "title": "Bryson – Zone-Constrained Categorical Facies",
        "subtitle": "7 wells · Appalachian Basin · no-crossing constraint",
        "description": (
            "Correlate using categorical FACIES with ZONE no-crossing:\n"
            "• ZONE no-crossing = biozone datums cannot swap order.\n"
            "• Categorical cost: facies identity mismatch penalty.\n"
            "Red lines show ZONE/MEMBER boundaries. Gaps = non-deposition."
        ),
        "geology_note": (
            "Setting: Appalachian Basin (Devonian–Carboniferous).\n"
            "Purely categorical correlation — no continuous wireline logs.\n"
            "Data: FACIES (lithofacies codes), MEMBER (lithostratigraphic member),\n"
            "ZONE (biostratigraphic zone — time-equivalent), SEQSTRAT (sequence boundaries).\n"
            "ZONE no-crossing enforces that biozone datums do not swap order,\n"
            "demonstrating integration of bio- and litho-stratigraphy.\n"
            "Reference: Bryson (2000), Haq et al. (1987)."
        ),
        "wells": DATA_DIR / "data_set_bryson" / "wells.txt",
        "runs": [
            {"name": "FACIES + ZONE nc", "opts": {
                "var_data": "FACIES", "no_crossing": "ZONE"}},
            {"name": "FACIES unconstrained", "opts": {
                "var_data": "FACIES"}},
        ],
        "common_opts": {"cost_function": "composite",
                        "max_cor": 200, "nbr_cor": 100, "out_nbr_cor": 20,
                        "min_dist": 0.1, "out_min_dist": 0.05},
        "ai": {"quality": True, "anomaly": True, "uncertainty": False, "log_qc": False},
    },
    "9_sigrun": {
        "title": "Sigrun – Multi-Log Well-Tie (GR+NPHI)",
        "subtitle": "2 wells · SEQUENCE boundaries",
        "description": (
            "Correlate SEQUENCE boundaries in a North Sea well pair.\n"
            "Red lines = sequence boundary / MFS positions.\n"
            "Gaps indicate condensed sections or erosion at unconformities.\n"
            "The 2-well case shows pure DTW alignment for well-tie."
        ),
        "geology_note": (
            "Setting: Gudrun-Sigrun area, Northern North Sea (Upper Jurassic).\n"
            "Hugin and Draupne Formations — marine shale/sand sequence.\n"
            "2-well correlation demonstrates DTW well-tie (log-shape matching)\n"
            "without structural control — purely data-driven alignment.\n"
            "Sequence boundaries and maximum flooding surfaces (MFS) are the\n"
            "primary correlation targets.\n"
            "Reference: Kieft et al. (2010), Partington et al. (1993)."
        ),
        "wells": DATA_DIR / "data_set_sigrun" / "wells.txt",
        "runs": [
            {"name": "GR+NPHI", "opts": {
                "var_data": "GR", "var_weight": 0.6,
                "var_data2": "NPHI", "var_weight2": 0.4}},
            {"name": "GR only", "opts": {
                "var_data": "GR", "var_weight": 1.0}},
        ],
        "common_opts": {"cost_function": "composite",
                        "max_cor": 200, "nbr_cor": 100, "out_nbr_cor": 20,
                        "min_dist": 0.1, "out_min_dist": 0.05},
        "ai": {"quality": True, "anomaly": False, "uncertainty": False, "log_qc": False},
    },
    "10_troll": {
        "title": "Troll – Categorical + Distality (Walther's Law)",
        "subtitle": "5 wells · categorical correlation",
        "description": (
            "Correlate BIOZONE and SEQUENCE boundaries using categorical\n"
            "facies + distality (Walther's Law: facies belts shift predictably).\n"
            "Red lines = facies-belt transitions (boundary correlation).\n"
            "Gaps = condensation in distal or bypass in proximal wells."
        ),
        "geology_note": (
            "Setting: Troll field, Northern North Sea (Sognefjord Formation, Upper Jurassic).\n"
            "Thick sand reservoir (~150 m) with lateral facies transitions.\n"
            "Walther's Law: vertical facies succession mirrors lateral facies belts —\n"
            "the distality cost function encodes this principle mathematically.\n"
            "No continuous logs — demonstrates that categorical facies + distality\n"
            "ordering alone can drive meaningful stratigraphic correlation.\n"
            "Reference: Dreyer et al. (2005), Holgate et al. (2013)."
        ),
        "wells": DATA_DIR / "data_set_troll" / "wells.txt",
        "runs": [
            {"name": "FACIES+DISTALITY", "opts": {
                "var_data": "FACIES", "var_weight": 0.6,
                "var_data2": "DISTALITY", "var_weight2": 0.4}},
            {"name": "FACIES only", "opts": {
                "var_data": "FACIES", "var_weight": 1.0}},
        ],
        "common_opts": {"cost_function": "composite",
                        "max_cor": 200, "nbr_cor": 100, "out_nbr_cor": 20,
                        "min_dist": 0.1, "out_min_dist": 0.05},
        "ai": {"quality": True, "anomaly": False, "uncertainty": False, "log_qc": False},
    },
    "11_hugin_tidal": {
        "title": "Hugin Fm – Tidal Distality (Real Wells)",
        "subtitle": "2 real North Sea wells · distality cost",
        "description": (
            "Real subsurface wells from the Hugin Formation (Gudrun–Sigrun area).\n"
            "Tide-dominated shallow marine with interpreted depositional facies.\n"
            "Demonstrates distality cost on real data — Walther's Law constrains\n"
            "lateral facies ordering across the proximal-distal gradient."
        ),
        "geology_note": (
            "Setting: Hugin Formation, South Viking Graben (Upper Jurassic).\n"
            "Tide-dominated shallow marine to coastal plain succession.\n"
            "Wells span proximal (tidal channel/bar) to distal (prodelta/offshore).\n"
            "Reference: Baville et al. (2024), EAGE Annual Conference, Oslo."
        ),
        "wells": DATA_DIR / "data_set_hugin_tidal" / "facies.wells.txt",
        "runs": [
            {"name": "Distality (FACIES_1)", "opts": {
                "dist_distal": "DISTALITY", "dist_facies": "FACIES_1", "dist_scaling": 1.0}},
            {"name": "Distality (FACIES_2)", "opts": {
                "dist_distal": "DISTALITY", "dist_facies": "FACIES_2", "dist_scaling": 1.0}},
        ],
        "common_opts": {"cost_function": "composite", "order": "distality",
                        "max_cor": 200, "nbr_cor": 100, "out_nbr_cor": 20,
                        "min_dist": 0.1, "out_min_dist": 0.05},
        "ai": {"quality": True, "anomaly": False, "uncertainty": False, "log_qc": False},
    },
}


# ═══════════════════════════════════════════════════════════════════════════
#  Worker Thread
# ═══════════════════════════════════════════════════════════════════════════

class CorrelationWorker(QThread):
    """Run a single correlation in a background thread."""
    finished = pyqtSignal(str, object, object, str)  # run_name, res_file, well_list, log_text
    progress = pyqtSignal(str)  # status message

    def __init__(self, run_name, wells_path, opts, parent=None):
        super().__init__(parent)
        self.run_name = run_name
        self.wells_path = wells_path
        self.opts = opts

    def run(self):
        log_buf = io.StringIO()
        try:
            with redirect_stdout(log_buf), redirect_stderr(log_buf):
                project = ProjectExt()
                project.set_options_ext(**RESET_OPTS)
                project.set_options_ext(**self.opts)
                project.run(str(self.wells_path))

            res_file = project.get_res_file()
            well_list = WellList(str(self.wells_path))
            self.finished.emit(self.run_name, res_file, well_list, log_buf.getvalue())
        except Exception as e:
            self.finished.emit(self.run_name, None, None, f"ERROR: {e}\n{log_buf.getvalue()}")


# ═══════════════════════════════════════════════════════════════════════════
#  Plot Generation
# ═══════════════════════════════════════════════════════════════════════════

def generate_plot(well_list, res_file, title, data_name=None, depth_name=None,
                  cor_index=0, plot_settings=None):
    """Generate a correlation plot with narrow well columns, clear log headers
    with units, depth labels, and correlation lines drawn ONLY in dedicated
    gap corridors between wells."""
    if res_file is None or res_file.get_nbr_results() == 0:
        return None

    ps = plot_settings or {}
    n_wells = len(res_file.well_id)
    wells = [well_list.wells[wid] for wid in res_file.well_id]

    SKIP_DATA = {"Depth", "DEPTH", "MD", "TVD", "TVDSS", "X", "Y", "Z"}
    ZONE_CMAP = plt.cm.Set3.colors  # 12 distinct pastel colors

    # Known log units (fallback when meta not available)
    KNOWN_UNITS = {
        "GR": "API", "RT": "ohm.m", "RESD": "ohm.m", "RESS": "ohm.m",
        "DEN": "g/cc", "RHOB": "g/cc", "DT": "us/ft", "SP": "mV",
        "SPT": "mV", "NPHI": "v/v", "NEU": "v/v", "CALI": "in",
        "ILD": "ohm.m", "ILM": "ohm.m", "PHIE": "v/v", "SW": "v/v",
        "VSH": "v/v", "PERM": "mD",
    }

    def get_depth(well):
        for dn in ("TVDSS", "TVD", "Depth", "DEPTH", "MD"):
            if dn in well.data and well.data[dn]:
                return list(well.data[dn]), dn
        return list(range(well.size)), "Index"

    depth_info = [get_depth(w) for w in wells]
    depths = [d[0] for d in depth_info]
    depth_labels = [d[1] for d in depth_info]

    # Identify log channels (up to 3) for display
    max_logs = ps.get('max_logs', 3)

    def get_log_names(well, n=3):
        names = []
        for k in well.data:
            if k not in SKIP_DATA and len(names) < n:
                names.append(k)
        return names

    log_names = get_log_names(wells[0], max_logs)

    # Get unit for a log name
    def get_unit(well, lname):
        if hasattr(well, 'meta') and lname in well.meta:
            return well.meta[lname].get('uom', KNOWN_UNITS.get(lname.upper(), ''))
        return KNOWN_UNITS.get(lname.upper(), '')

    # Identify regions for zone bands (up to 2)
    def get_region_names(well, max_regions=2):
        return list(well.region.keys())[:max_regions] if hasattr(well, 'region') and well.region else []

    region_names = get_region_names(wells[0])
    has_zones = bool(region_names)

    # Build facies dictionary for primary region (used for facies strip)
    from weco.facies_dict import FaciesDictionary
    facies_dict = None
    facies_region = None
    if has_zones:
        facies_region = region_names[0]
        facies_dict = FaciesDictionary.from_region_auto(facies_region, wells)

    # ── GridSpec layout: [facies|log | gap | facies|log | gap | ...] ──
    # Each well = thin facies strip + log track; gap corridors between
    n_gaps = n_wells - 1
    log_width = ps.get('log_width', 0.5)   # user-adjustable well column width
    gap_width = ps.get('gap_width', 1.2)   # gap corridor width (where lines go)
    facies_width = ps.get('facies_width', 0.12) if has_zones else 0.0
    width_ratios = []
    for i in range(n_wells):
        if has_zones:
            width_ratios.append(facies_width)  # facies strip
        width_ratios.append(log_width)         # log track
        if i < n_gaps:
            width_ratios.append(gap_width)     # gap corridor
    total_cols = len(width_ratios)

    fig_width = max(10, (log_width + facies_width) * n_wells * 2.0 + gap_width * n_gaps * 1.5 + 2)
    fig_width = min(fig_width, 28)  # cap to prevent memory overflow
    fig_height = ps.get('fig_height', 9)
    fig_height = min(fig_height, 14)
    fig = plt.figure(figsize=(fig_width, fig_height))
    gs = fig.add_gridspec(1, total_cols, width_ratios=width_ratios,
                          wspace=0.02, left=0.05, right=0.97, top=0.86, bottom=0.07)

    fig.suptitle(title, fontsize=11, fontweight="bold", y=0.96)

    # Create well axes, facies axes, and gap axes
    well_axes = []
    facies_axes = []
    gap_axes = []
    col_idx = 0
    for i in range(n_wells):
        if has_zones:
            fax = fig.add_subplot(gs[0, col_idx])
            fax.set_xlim(0, 1)
            fax.tick_params(left=False, labelleft=False, bottom=False, labelbottom=False)
            for spine in fax.spines.values():
                spine.set_linewidth(0.3)
            facies_axes.append(fax)
            col_idx += 1
        ax = fig.add_subplot(gs[0, col_idx])
        well_axes.append(ax)
        col_idx += 1
        if i < n_gaps:
            gax = fig.add_subplot(gs[0, col_idx])
            gax.set_xlim(0, 1)
            gax.axis('off')
            gap_axes.append(gax)
            col_idx += 1

    axes = well_axes  # for compatibility

    # ── Draw wells ────────────────────────────────────────────────────
    for i, (well, ax, depth) in enumerate(zip(wells, axes, depths)):
        ax.invert_yaxis()

        # ── Header: well name + log names with units ──────────────────
        header_lines = [well.name]
        log_header_parts = []
        for li, lname in enumerate(log_names):
            if lname in well.data:
                unit = get_unit(well, lname)
                color = LOG_COLORS[li % len(LOG_COLORS)]
                label = f"{lname}" + (f" [{unit}]" if unit else "")
                log_header_parts.append((label, color))
        # Well name at top, log names above it (stacked upward)
        ax.set_title(well.name, fontsize=8, fontweight="bold",
                     color=WELL_COLORS[i % 10],
                     pad=4 + len(log_header_parts) * 10)
        # Log names above well title
        for li, (label, color) in enumerate(log_header_parts):
            ax.text(0.5, 1.02 + li * 0.04, label, transform=ax.transAxes,
                    fontsize=7, ha='center', va='bottom', color=color,
                    fontweight='medium')

        # ── Depth axis (y-axis) ───────────────────────────────────────
        if i == 0:
            ax.set_ylabel(depth_labels[i], fontsize=7, labelpad=2)
        ax.tick_params(axis='y', labelsize=6, length=2, pad=1)
        # Show depth ticks on all wells for readability
        ax.tick_params(axis='y', labelleft=True)
        ax.yaxis.set_major_locator(plt.MaxNLocator(nbins=8, integer=True))

        # ── Zone/region background bands (light tint on log axis) ─────
        if has_zones:
            for ri, rname in enumerate(region_names):
                if hasattr(well, 'region') and rname in well.region:
                    raw_region = list(well.region[rname])
                    if raw_region and isinstance(raw_region[0], (list, tuple)) and len(raw_region[0]) >= 3:
                        for entry in raw_region:
                            val, start, count = entry[0], entry[1], entry[2]
                            if val is None:
                                continue
                            end = start + count - 1
                            y_top = depth[start] if start < len(depth) else start
                            y_bot = depth[min(end, len(depth)-1)] if end < len(depth) else end
                            color = facies_dict.get_color(int(val)) if facies_dict else ZONE_CMAP[int(val) % len(ZONE_CMAP)]
                            alpha = 0.15 if ri == 0 else 0.08
                            ax.axhspan(y_top, y_bot, color=color,
                                       alpha=alpha, zorder=0)

        # ── Facies strip (discrete coloured column left of logs) ──────
        if has_zones and facies_axes:
            fax = facies_axes[i]
            fax.invert_yaxis()
            fax.set_ylim(ax.get_ylim())
            if facies_region and hasattr(well, 'region') and facies_region in well.region:
                raw_region = list(well.region[facies_region])
                if raw_region and isinstance(raw_region[0], (list, tuple)) and len(raw_region[0]) >= 3:
                    for entry in raw_region:
                        val, start, count = entry[0], entry[1], entry[2]
                        if val is None:
                            continue
                        end = start + count - 1
                        y_top = depth[start] if start < len(depth) else start
                        y_bot = depth[min(end, len(depth)-1)] if end < len(depth) else end
                        color = facies_dict.get_color(int(val))
                        fax.axhspan(y_top, y_bot, color=color, alpha=0.85)
                        # Label if band is tall enough
                        band_h = abs(y_bot - y_top)
                        total_h = abs(depth[-1] - depth[0]) if len(depth) > 1 else 1
                        if band_h / total_h > 0.04:
                            y_mid = (y_top + y_bot) / 2
                            label = facies_dict.get_label(int(val))
                            fax.text(0.5, y_mid, label[:6], fontsize=4.5,
                                     ha='center', va='center', color='#222',
                                     rotation=90 if band_h / total_h < 0.08 else 0,
                                     clip_on=True)
            if i == 0:
                fax.set_title(facies_region, fontsize=6, pad=2)

        # ── Log traces ────────────────────────────────────────────────
        plotted_logs = []
        for li, lname in enumerate(log_names):
            if lname in well.data:
                vals = list(well.data[lname])[:len(depth)]
                if vals:
                    color = LOG_COLORS[li % len(LOG_COLORS)]
                    ax.plot(vals, depth[:len(vals)], color=color,
                            linewidth=0.8 if li > 0 else 1.0,
                            alpha=0.9 if li == 0 else 0.6,
                            label=lname, clip_on=True)
                    plotted_logs.append(lname)

        if not plotted_logs:
            ax.set_xlim(-0.5, 0.5)
            ax.axvline(0, color=WELL_COLORS[i % 10], linewidth=2)

        # Show only min/max x-tick to avoid clutter
        ax.xaxis.set_major_locator(plt.MaxNLocator(nbins=3))
        ax.tick_params(axis='x', labelsize=5, length=2, rotation=0)
        ax.grid(True, alpha=0.12, linewidth=0.3)
        # Clip log traces tightly within the axes
        ax.set_clip_on(True)
        for spine in ax.spines.values():
            spine.set_linewidth(0.5)

    # ── Draw correlation lines ────────────────────────────────────────
    # Supports uncertainty overlay: draw top-N results with decreasing alpha
    show_uncertainty = ps.get('show_uncertainty', True)
    n_overlay = min(ps.get('uncertainty_n', 3), res_file.get_nbr_results()) if show_uncertainty else 1

    cid = min(cor_index, res_file.get_nbr_results() - 1)
    cost = res_file.get_result_cost(cid)

    # Build boundary indices from primary (coarsest) region
    boundary_indices = [set() for _ in range(n_wells)]
    primary_region = None
    if region_names:
        best_rname, best_count = None, 999999
        for rname in region_names:
            total_intervals = sum(
                len(list(w.region[rname])) for w in wells
                if hasattr(w, 'region') and rname in w.region
            )
            if 0 < total_intervals < best_count:
                best_count = total_intervals
                best_rname = rname
        primary_region = best_rname

    for wi, well in enumerate(wells):
        if primary_region and hasattr(well, 'region') and primary_region in well.region:
            rlist = list(well.region[primary_region])
            if rlist and isinstance(rlist[0], (list, tuple)) and len(rlist[0]) >= 3:
                for entry in rlist:
                    boundary_indices[wi].add(entry[1])

    clr_boundary = ps.get('boundary', '#D32F2F')
    clr_gap = ps.get('gap', '#1565C0')
    clr_framework = ps.get('framework', '#999999')

    # Alpha scaling for overlay: primary=1.0, secondary fading
    overlay_alphas = [1.0, 0.3, 0.15, 0.08][:n_overlay]
    overlay_indices = [cid]
    # Add other results for uncertainty overlay (skip duplicates)
    if n_overlay > 1:
        for alt in range(res_file.get_nbr_results()):
            if alt != cid and len(overlay_indices) < n_overlay:
                overlay_indices.append(alt)

    for ov_idx, result_idx in enumerate(overlay_indices):
        alpha_scale = overlay_alphas[ov_idx]
        path = res_file.get_result_full_path(result_idx)

        # Classify steps for this result
        boundary_steps = set()
        boundary_scores = {}
        gap_steps = set()
        min_gap_run = max(3, len(path) // 80)

        for step_idx in range(1, len(path)):
            node = path[step_idx]
            prev = path[step_idx - 1]
            score = 0
            for wi in range(n_wells):
                if node[wi] in boundary_indices[wi] and node[wi] != prev[wi]:
                    score += 1
            if score > 0:
                boundary_steps.add(step_idx)
                boundary_scores[step_idx] = score

        MAX_BOUNDARY_LINES = ps.get('max_boundaries', 30)
        if len(boundary_steps) > MAX_BOUNDARY_LINES:
            ranked = sorted(boundary_steps, key=lambda s: boundary_scores.get(s, 0), reverse=True)
            boundary_steps = set(ranked[:MAX_BOUNDARY_LINES])

        for wi in range(n_wells):
            run_start = None
            for step_idx in range(1, len(path)):
                stayed = (path[step_idx][wi] == path[step_idx - 1][wi])
                if stayed:
                    if run_start is None:
                        run_start = step_idx
                else:
                    if run_start is not None and (step_idx - run_start) >= min_gap_run:
                        gap_steps.add((run_start + step_idx) // 2)
                    run_start = None
            if run_start is not None and (len(path) - run_start) >= min_gap_run:
                gap_steps.add((run_start + len(path) - 1) // 2)

        gap_steps -= boundary_steps
        MAX_GAP_LINES = ps.get('max_gaps', 20)
        if len(gap_steps) > MAX_GAP_LINES:
            gap_steps = set(sorted(gap_steps)[:MAX_GAP_LINES])

        n_path = len(path)
        # Framework lines only for primary result
        if ov_idx == 0:
            framework_interval = max(1, n_path // 6)
            framework_steps = {s for s in range(0, n_path, framework_interval)}
            framework_steps.add(0)
            framework_steps.add(n_path - 1)
            framework_steps -= boundary_steps
            framework_steps -= gap_steps
        else:
            framework_steps = set()  # no framework for overlay results

        # Draw lines in gap corridors
        for step_idx in sorted(boundary_steps | gap_steps | framework_steps):
            node = path[step_idx]
            if step_idx in boundary_steps:
                color, alpha, lw = clr_boundary, 0.85 * alpha_scale, 1.4 * alpha_scale + 0.3
            elif step_idx in gap_steps:
                color, alpha, lw = clr_gap, 0.6 * alpha_scale, 1.0 * alpha_scale + 0.2
            else:
                color, alpha, lw = clr_framework, 0.25, 0.4

            ls = ":" if step_idx in gap_steps else "-"

            for j in range(n_wells - 1):
                ml = node[j]
                mr = node[j + 1]
                if ml < len(depths[j]) and mr < len(depths[j + 1]):
                    yl = depths[j][ml]
                    yr = depths[j + 1][mr]
                    gap_ax = gap_axes[j]
                    y_range_l = (min(depths[j]), max(depths[j]))
                    y_range_r = (min(depths[j + 1]), max(depths[j + 1]))
                    y_min = min(y_range_l[0], y_range_r[0])
                    y_max = max(y_range_l[1], y_range_r[1])
                    if y_max > y_min:
                        yl_norm = 1.0 - (yl - y_min) / (y_max - y_min)
                        yr_norm = 1.0 - (yr - y_min) / (y_max - y_min)
                    else:
                        yl_norm = yr_norm = 0.5
                    gap_ax.plot([0, 1], [yl_norm, yr_norm], color=color,
                                alpha=alpha, linewidth=lw, linestyle=ls,
                                clip_on=True, transform=gap_ax.transAxes)

    # Gap axes are display-only with transAxes lines — no ylim inversion needed

    # ── Legend ─────────────────────────────────────────────────────────
    from matplotlib.lines import Line2D
    legend_elements = []
    # Log legend entries
    for li, lname in enumerate(log_names):
        unit = get_unit(wells[0], lname)
        label = f"{lname}" + (f" [{unit}]" if unit else "")
        legend_elements.append(Line2D([0], [0], color=LOG_COLORS[li % len(LOG_COLORS)],
                                      linewidth=1.2, label=label))
    # Correlation line legend
    legend_elements.append(Line2D([0], [0], color=clr_boundary, linewidth=1.4,
                                  label='Unit boundary'))
    legend_elements.append(Line2D([0], [0], color=clr_gap, linewidth=1.0,
                                  linestyle=':', label='Gap/hiatus'))
    legend_elements.append(Line2D([0], [0], color=clr_framework, linewidth=0.5,
                                  label='Framework'))
    if n_overlay > 1:
        legend_elements.append(Line2D([0], [0], color='#999', linewidth=0.8,
                                      alpha=0.3, label=f'Uncertainty (top-{n_overlay})'))
    fig.legend(handles=legend_elements, loc='lower center', ncol=min(len(legend_elements), 7),
               fontsize=7, framealpha=0.8, edgecolor='#ccc',
               bbox_to_anchor=(0.5, 0.002))

    # ── Title subtitle ────────────────────────────────────────────────
    subtitle_parts = [f"Correlation #{cid}", f"Cost: {cost:.4f}",
                      f"{res_file.get_nbr_results()} solutions"]
    if primary_region:
        subtitle_parts.append(f"Boundaries: {primary_region}")
    fig.text(0.5, 0.92, "  |  ".join(subtitle_parts),
             ha="center", fontsize=8, color="#555")

    # Save to output dir
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    safe_title = title.replace(" ", "_").replace("/", "_").replace("\n", "_")[:60]
    out_path = OUTPUT_DIR / f"gui_{safe_title}.png"
    fig.savefig(str(out_path), dpi=130, bbox_inches="tight")
    plt.close(fig)
    return str(out_path)


# ═══════════════════════════════════════════════════════════════════════════
#  Main GUI
# ═══════════════════════════════════════════════════════════════════════════

class DemoRunnerWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"WeCo {get_version()} — Demo Runner")
        self.setMinimumSize(1100, 700)
        self.resize(1300, 800)

        self._workers = []
        self._run_results = {}  # {run_name: (res_file, well_list, cost)}
        self._last_res_file = None
        self._last_well_list = None

        self._init_ui()

    def _init_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QHBoxLayout(central)
        main_layout.setContentsMargins(6, 6, 6, 6)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        main_layout.addWidget(splitter)

        # ── Left panel: dataset tree ──────────────────────────────────
        left_panel = QWidget()
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(0, 0, 0, 0)

        header = QLabel("Demo Datasets")
        header.setFont(QFont("", 12, QFont.Weight.Bold))
        left_layout.addWidget(header)

        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Dataset / Run"])
        self.tree.setMinimumWidth(260)
        self.tree.itemClicked.connect(self._on_tree_click)
        left_layout.addWidget(self.tree)

        # Populate tree
        for ds_key, ds in DATASETS.items():
            parent = QTreeWidgetItem(self.tree, [ds["title"]])
            parent.setData(0, Qt.ItemDataRole.UserRole, ds_key)
            parent.setFont(0, QFont("", 10, QFont.Weight.Bold))
            parent.setToolTip(0, ds["description"])
            for run in ds["runs"]:
                child = QTreeWidgetItem(parent, [f"  {run['name']}"])
                child.setData(0, Qt.ItemDataRole.UserRole, (ds_key, run["name"]))
            parent.setExpanded(True)

        # Buttons
        btn_layout = QHBoxLayout()
        self.btn_run_selected = QPushButton("Run Selected")
        self.btn_run_selected.clicked.connect(self._run_selected)
        self.btn_run_selected.setStyleSheet("QPushButton { font-weight: bold; padding: 6px; }")
        btn_layout.addWidget(self.btn_run_selected)

        self.btn_run_all = QPushButton("Run All")
        self.btn_run_all.clicked.connect(self._run_all)
        btn_layout.addWidget(self.btn_run_all)

        left_layout.addLayout(btn_layout)

        splitter.addWidget(left_panel)

        # ── Right panel: tabs (info / parameters / results / log) ─────
        right_panel = QWidget()
        right_layout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(0, 0, 0, 0)

        self.tabs = QTabWidget()
        right_layout.addWidget(self.tabs)

        # Tab 1: Info
        self.info_widget = QWidget()
        info_layout = QVBoxLayout(self.info_widget)
        self.info_title = QLabel("Select a dataset from the tree")
        self.info_title.setFont(QFont("", 13, QFont.Weight.Bold))
        info_layout.addWidget(self.info_title)
        self.info_desc = QLabel("")
        self.info_desc.setWordWrap(True)
        self.info_desc.setFont(QFont("Monospace", 10))
        info_layout.addWidget(self.info_desc)

        self.info_geology = QLabel("")
        self.info_geology.setWordWrap(True)
        self.info_geology.setFont(QFont("", 9))
        self.info_geology.setStyleSheet("color: #555; margin-top: 6px;")
        info_layout.addWidget(self.info_geology)

        self.info_wells = QLabel("")
        self.info_wells.setFont(QFont("", 10))
        info_layout.addWidget(self.info_wells)

        # Parameter override area
        self.param_group = QGroupBox("Parameters (editable before run)")
        param_layout = QFormLayout(self.param_group)
        self.param_widgets = {}
        # We'll populate dynamically based on selection
        info_layout.addWidget(self.param_group)

        # AI Features toggle area
        self.ai_group = QGroupBox("AI Features")
        ai_layout = QFormLayout(self.ai_group)
        self.ai_quality_cb = QCheckBox("Quality scoring")
        self.ai_quality_cb.setToolTip("Score and rank correlations by multi-criteria quality")
        ai_layout.addRow(self.ai_quality_cb)
        self.ai_anomaly_cb = QCheckBox("Anomaly detection")
        self.ai_anomaly_cb.setToolTip("Flag suspicious correlation lines (Isolation Forest)")
        ai_layout.addRow(self.ai_anomaly_cb)
        self.ai_uncertainty_cb = QCheckBox("Uncertainty analysis")
        self.ai_uncertainty_cb.setToolTip("N-best ensemble spread → per-marker confidence")
        ai_layout.addRow(self.ai_uncertainty_cb)
        self.ai_logqc_cb = QCheckBox("Log QC preprocessing")
        self.ai_logqc_cb.setToolTip("Washout detection, imputation, cross-well normalisation")
        ai_layout.addRow(self.ai_logqc_cb)
        info_layout.addWidget(self.ai_group)
        info_layout.addStretch()
        self.tabs.addTab(self.info_widget, "Info && Params")

        # Tab 2: Plot (result view) with paging
        results_widget = QWidget()
        results_vlayout = QVBoxLayout(results_widget)
        results_vlayout.setContentsMargins(4, 4, 4, 4)

        # ─ Paging controls (single compact row) ─
        paging_bar = QHBoxLayout()
        self.run_selector = QComboBox()
        self.run_selector.setMinimumWidth(120)
        self.run_selector.setMaximumWidth(260)
        self.run_selector.currentIndexChanged.connect(self._on_run_selector_changed)
        paging_bar.addWidget(QLabel("Run:"))
        paging_bar.addWidget(self.run_selector)

        paging_bar.addSpacing(10)
        self.btn_prev = QPushButton("◀")
        self.btn_prev.setFixedWidth(28)
        self.btn_prev.clicked.connect(self._page_prev)
        paging_bar.addWidget(self.btn_prev)

        self.result_spin = QSpinBox()
        self.result_spin.setPrefix("Result #")
        self.result_spin.setMinimum(0)
        self.result_spin.setMaximum(0)
        self.result_spin.valueChanged.connect(self._on_result_spin_changed)
        paging_bar.addWidget(self.result_spin)

        self.btn_next = QPushButton("▶")
        self.btn_next.setFixedWidth(28)
        self.btn_next.clicked.connect(self._page_next)
        paging_bar.addWidget(self.btn_next)

        paging_bar.addSpacing(10)
        self.result_cost_label = QLabel("")
        self.result_cost_label.setFont(QFont("Monospace", 9))
        paging_bar.addWidget(self.result_cost_label)
        paging_bar.addStretch()

        results_vlayout.addLayout(paging_bar)

        # ─ Plot settings panel (collapsible) ─
        settings_group = QGroupBox("Plot Settings")
        settings_group.setCheckable(True)
        settings_group.setChecked(False)  # collapsed by default
        settings_layout = QGridLayout(settings_group)
        settings_layout.setContentsMargins(6, 4, 6, 4)
        settings_layout.setSpacing(4)

        # Log color controls
        settings_layout.addWidget(QLabel("Log Colors:"), 0, 0)
        self._log_color_btns = []
        for li in range(3):
            btn = QPushButton(f"Log {li+1}")
            btn.setFixedSize(60, 22)
            btn.setStyleSheet(f"background-color: {LOG_COLORS[li]}; color: white; font-size: 9px;")
            btn.clicked.connect(lambda checked, idx=li: self._pick_log_color(idx))
            settings_layout.addWidget(btn, 0, li + 1)
            self._log_color_btns.append(btn)

        # Correlation line colors
        settings_layout.addWidget(QLabel("Boundary:"), 1, 0)
        self._boundary_color_btn = QPushButton("")
        self._boundary_color_btn.setFixedSize(60, 22)
        self._boundary_color_btn.setStyleSheet("background-color: #D32F2F; color: white; font-size: 9px;")
        self._boundary_color_btn.clicked.connect(lambda: self._pick_line_color('boundary'))
        settings_layout.addWidget(self._boundary_color_btn, 1, 1)

        settings_layout.addWidget(QLabel("Gap/Hiatus:"), 1, 2)
        self._gap_color_btn = QPushButton("")
        self._gap_color_btn.setFixedSize(60, 22)
        self._gap_color_btn.setStyleSheet("background-color: #1565C0; color: white; font-size: 9px;")
        self._gap_color_btn.clicked.connect(lambda: self._pick_line_color('gap'))
        settings_layout.addWidget(self._gap_color_btn, 1, 3)

        settings_layout.addWidget(QLabel("Framework:"), 1, 4)
        self._framework_color_btn = QPushButton("")
        self._framework_color_btn.setFixedSize(60, 22)
        self._framework_color_btn.setStyleSheet("background-color: #999999; font-size: 9px;")
        self._framework_color_btn.clicked.connect(lambda: self._pick_line_color('framework'))
        settings_layout.addWidget(self._framework_color_btn, 1, 5)

        # Max lines controls
        settings_layout.addWidget(QLabel("Max boundaries:"), 2, 0)
        self._max_boundaries_spin = QSpinBox()
        self._max_boundaries_spin.setRange(5, 100)
        self._max_boundaries_spin.setValue(30)
        self._max_boundaries_spin.setFixedWidth(60)
        settings_layout.addWidget(self._max_boundaries_spin, 2, 1)

        settings_layout.addWidget(QLabel("Max gaps:"), 2, 2)
        self._max_gaps_spin = QSpinBox()
        self._max_gaps_spin.setRange(5, 100)
        self._max_gaps_spin.setValue(20)
        self._max_gaps_spin.setFixedWidth(60)
        settings_layout.addWidget(self._max_gaps_spin, 2, 3)

        # Refresh button
        self._refresh_btn = QPushButton("Refresh Plot")
        self._refresh_btn.setFixedWidth(90)
        self._refresh_btn.clicked.connect(self._display_current_result)
        settings_layout.addWidget(self._refresh_btn, 2, 5)

        results_vlayout.addWidget(settings_group)

        # ─ Plot area ─
        self.plot_scroll = QScrollArea()
        self.plot_scroll.setWidgetResizable(True)
        self.plot_container = QWidget()
        self.plot_layout = QVBoxLayout(self.plot_container)
        self.plot_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.plot_scroll.setWidget(self.plot_container)
        results_vlayout.addWidget(self.plot_scroll, 1)

        # ─ Cost ranking bar (bottom) ─
        self.ranking_label = QLabel("")
        self.ranking_label.setFont(QFont("Monospace", 8))
        self.ranking_label.setWordWrap(True)
        self.ranking_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        results_vlayout.addWidget(self.ranking_label)

        # ─ Well selection and ordering ─
        well_group = QGroupBox("Wells (select and reorder)")
        well_group.setCheckable(True)
        well_group.setChecked(False)
        well_lo = QVBoxLayout(well_group)
        well_lo.setContentsMargins(4, 4, 4, 4)
        self._well_list_widget = QListWidget()
        self._well_list_widget.setDragDropMode(QListWidget.DragDropMode.InternalMove)
        self._well_list_widget.setSelectionMode(QListWidget.SelectionMode.MultiSelection)
        self._well_list_widget.setMaximumHeight(100)
        well_lo.addWidget(self._well_list_widget)
        well_btn_lo = QHBoxLayout()
        btn_all = QPushButton("All")
        btn_all.setFixedWidth(50)
        btn_all.clicked.connect(self._select_all_wells)
        well_btn_lo.addWidget(btn_all)
        btn_none = QPushButton("None")
        btn_none.setFixedWidth(50)
        btn_none.clicked.connect(self._select_no_wells)
        well_btn_lo.addWidget(btn_none)
        btn_up = QPushButton("▲")
        btn_up.setFixedWidth(30)
        btn_up.clicked.connect(self._move_well_up)
        well_btn_lo.addWidget(btn_up)
        btn_down = QPushButton("▼")
        btn_down.setFixedWidth(30)
        btn_down.clicked.connect(self._move_well_down)
        well_btn_lo.addWidget(btn_down)
        btn_apply = QPushButton("Apply")
        btn_apply.clicked.connect(self._apply_well_selection)
        well_btn_lo.addWidget(btn_apply)
        well_btn_lo.addStretch()
        well_lo.addLayout(well_btn_lo)
        results_vlayout.addWidget(well_group)

        self.tabs.addTab(results_widget, "Results")

        # Tab 3: Engine Log
        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setFont(QFont("Monospace", 9))
        self.tabs.addTab(self.log_text, "Engine Log")

        # Tab 4: RDDMS Export
        export_widget = QWidget()
        export_layout = QVBoxLayout(export_widget)

        export_group = QGroupBox("RDDMS / OSDU Export")
        export_form = QFormLayout()

        self._rddms_url_edit = QLineEdit()
        self._rddms_url_edit.setPlaceholderText(
            "https://reservoir-ddms.interop.radix.equinor.com/api/v2")
        export_form.addRow("RDDMS URL:", self._rddms_url_edit)

        self._rddms_dataspace_edit = QLineEdit("maap/weco")
        export_form.addRow("Dataspace:", self._rddms_dataspace_edit)

        self._rddms_cor_num_spin = QSpinBox()
        self._rddms_cor_num_spin.setRange(0, 99)
        self._rddms_cor_num_spin.setValue(0)
        self._rddms_cor_num_spin.setToolTip(
            "Realisation index (0 = best). Each gets a unique UUID set.")
        export_form.addRow("Realisation (cor_num):", self._rddms_cor_num_spin)

        self._rddms_markers_cb = QCheckBox("Well markers (WellboreMarkerFrame)")
        self._rddms_markers_cb.setChecked(True)
        export_form.addRow(self._rddms_markers_cb)

        self._rddms_zonation_cb = QCheckBox("Zone log (DiscreteProperty)")
        self._rddms_zonation_cb.setChecked(True)
        export_form.addRow(self._rddms_zonation_cb)

        self._rddms_strat_cb = QCheckBox("Strat column (StratigraphicColumn)")
        self._rddms_strat_cb.setChecked(True)
        export_form.addRow(self._rddms_strat_cb)

        export_group.setLayout(export_form)
        export_layout.addWidget(export_group)

        # Export button
        export_btn_layout = QHBoxLayout()
        self._rddms_export_btn = QPushButton("Export to RDDMS")
        self._rddms_export_btn.setEnabled(False)
        self._rddms_export_btn.clicked.connect(self._export_to_rddms)
        export_btn_layout.addWidget(self._rddms_export_btn)
        export_layout.addLayout(export_btn_layout)

        # Status
        self._rddms_status_label = QLabel("")
        export_layout.addWidget(self._rddms_status_label)
        export_layout.addStretch()

        self.tabs.addTab(export_widget, "RDDMS Export")

        # Tab 5: Settings
        self._build_settings_tab()

        # Progress bar
        self.progress = QProgressBar()
        self.progress.setVisible(False)
        self.progress.setTextVisible(True)
        right_layout.addWidget(self.progress)

        splitter.addWidget(right_panel)
        splitter.setSizes([300, 1000])

        # Load saved settings
        self._load_settings()

        self.statusBar().showMessage("Ready — select a dataset and click Run")

    # ─── Settings Tab ─────────────────────────────────────────────────

    def _build_settings_tab(self):
        """Build the Settings tab with appearance, RDDMS, and display options."""
        from PyQt6.QtWidgets import QRadioButton, QButtonGroup

        settings_widget = QWidget()
        settings_layout = QVBoxLayout(settings_widget)

        # ── Appearance ──────────────────────────────────────────────
        appearance_group = QGroupBox("Appearance")
        appearance_form = QFormLayout()

        self._theme_group = QButtonGroup(self)
        self._theme_system = QRadioButton("System (follow OS)")
        self._theme_light = QRadioButton("Light")
        self._theme_dark = QRadioButton("Dark")
        self._theme_group.addButton(self._theme_system, 0)
        self._theme_group.addButton(self._theme_light, 1)
        self._theme_group.addButton(self._theme_dark, 2)
        self._theme_system.setChecked(True)

        theme_layout = QHBoxLayout()
        theme_layout.addWidget(self._theme_system)
        theme_layout.addWidget(self._theme_light)
        theme_layout.addWidget(self._theme_dark)
        appearance_form.addRow("Theme:", theme_layout)

        self._theme_group.idClicked.connect(self._on_theme_changed)
        appearance_group.setLayout(appearance_form)
        settings_layout.addWidget(appearance_group)

        # ── RDDMS Connection ────────────────────────────────────────
        rddms_group = QGroupBox("RDDMS Connection (saved locally)")
        rddms_form = QFormLayout()

        self._settings_rddms_url = QLineEdit()
        self._settings_rddms_url.setPlaceholderText(
            "https://reservoir-ddms.interop.radix.equinor.com/api/v2")
        rddms_form.addRow("Server URL:", self._settings_rddms_url)

        self._settings_rddms_dataspace = QLineEdit("maap/weco")
        rddms_form.addRow("Default dataspace:", self._settings_rddms_dataspace)

        self._settings_rddms_timeout = QSpinBox()
        self._settings_rddms_timeout.setRange(5, 300)
        self._settings_rddms_timeout.setValue(60)
        self._settings_rddms_timeout.setSuffix(" s")
        rddms_form.addRow("Timeout:", self._settings_rddms_timeout)

        rddms_group.setLayout(rddms_form)
        settings_layout.addWidget(rddms_group)

        # ── Well Display Order ──────────────────────────────────────
        order_group = QGroupBox("Default Well Display Order")
        order_form = QFormLayout()

        self._settings_well_order = QComboBox()
        self._settings_well_order.addItems([
            "Input order",
            "By X coordinate (West → East)",
            "By Y coordinate (South → North)",
            "By azimuth projection",
            "By distality (proximal → distal)",
            "Principal direction (PCA)",
            "Nearest-neighbour chain",
        ])
        self._settings_well_order.setToolTip(
            "Default display order for wells in correlation plots.\n"
            "Can be overridden per-run in the Results tab.")
        order_form.addRow("Order:", self._settings_well_order)

        self._settings_azimuth = QSpinBox()
        self._settings_azimuth.setRange(0, 359)
        self._settings_azimuth.setValue(90)
        self._settings_azimuth.setSuffix("° (N=0, E=90, S=180, W=270)")
        self._settings_azimuth.setToolTip(
            "Transport / depositional direction azimuth.\n"
            "Used when order = 'By azimuth projection'.")
        order_form.addRow("Azimuth:", self._settings_azimuth)

        order_group.setLayout(order_form)
        settings_layout.addWidget(order_group)

        # ── Engine Defaults ─────────────────────────────────────────
        engine_group = QGroupBox("Engine Defaults")
        engine_form = QFormLayout()

        self._settings_max_cor = QSpinBox()
        self._settings_max_cor.setRange(1, 500)
        self._settings_max_cor.setValue(50)
        self._settings_max_cor.setToolTip("Default max-cor (n-best search width)")
        engine_form.addRow("Max correlations:", self._settings_max_cor)

        self._settings_nbr_cor = QSpinBox()
        self._settings_nbr_cor.setRange(1, 100)
        self._settings_nbr_cor.setValue(10)
        self._settings_nbr_cor.setToolTip("Default number of output results")
        engine_form.addRow("Output n-best:", self._settings_nbr_cor)

        engine_group.setLayout(engine_form)
        settings_layout.addWidget(engine_group)

        # ── Save button ─────────────────────────────────────────────
        btn_layout = QHBoxLayout()
        save_btn = QPushButton("Save Settings")
        save_btn.clicked.connect(self._save_settings)
        btn_layout.addWidget(save_btn)
        btn_layout.addStretch()
        settings_layout.addLayout(btn_layout)

        self._settings_status = QLabel("")
        settings_layout.addWidget(self._settings_status)
        settings_layout.addStretch()

        self.tabs.addTab(settings_widget, "Settings")

    def _settings_file(self) -> Path:
        """Path to local settings JSON (stored in user config, not repo)."""
        config_dir = Path.home() / ".config" / "weco-gui"
        config_dir.mkdir(parents=True, exist_ok=True)
        return config_dir / "settings.json"

    def _save_settings(self):
        """Persist settings to a local JSON file."""
        import json
        settings = {
            "theme": self._theme_group.checkedId(),
            "rddms_url": self._settings_rddms_url.text(),
            "rddms_dataspace": self._settings_rddms_dataspace.text(),
            "rddms_timeout": self._settings_rddms_timeout.value(),
            "well_order": self._settings_well_order.currentIndex(),
            "azimuth": self._settings_azimuth.value(),
            "max_cor": self._settings_max_cor.value(),
            "nbr_cor": self._settings_nbr_cor.value(),
        }
        try:
            with open(self._settings_file(), "w") as f:
                json.dump(settings, f, indent=2)
            self._settings_status.setText("Settings saved.")
            # Sync RDDMS fields to Export tab
            self._rddms_url_edit.setText(settings["rddms_url"])
            self._rddms_dataspace_edit.setText(settings["rddms_dataspace"])
            self.statusBar().showMessage("Settings saved", 3000)
        except Exception as e:
            self._settings_status.setText(f"Error: {e}")

    def _load_settings(self):
        """Load settings from local JSON file if it exists."""
        import json
        path = self._settings_file()
        if not path.exists():
            return
        try:
            with open(path) as f:
                settings = json.load(f)
        except Exception:
            return

        # Apply loaded values
        theme_id = settings.get("theme", 0)
        btn = self._theme_group.button(theme_id)
        if btn:
            btn.setChecked(True)
        self._on_theme_changed(theme_id)

        if settings.get("rddms_url"):
            self._settings_rddms_url.setText(settings["rddms_url"])
            self._rddms_url_edit.setText(settings["rddms_url"])
        if settings.get("rddms_dataspace"):
            self._settings_rddms_dataspace.setText(settings["rddms_dataspace"])
            self._rddms_dataspace_edit.setText(settings["rddms_dataspace"])
        if "rddms_timeout" in settings:
            self._settings_rddms_timeout.setValue(settings["rddms_timeout"])
        if "well_order" in settings:
            self._settings_well_order.setCurrentIndex(settings["well_order"])
        if "azimuth" in settings:
            self._settings_azimuth.setValue(settings["azimuth"])
        if "max_cor" in settings:
            self._settings_max_cor.setValue(settings["max_cor"])
        if "nbr_cor" in settings:
            self._settings_nbr_cor.setValue(settings["nbr_cor"])

    def _on_theme_changed(self, theme_id: int):
        """Apply theme change live."""
        from PyQt6.QtGui import QPalette
        app = QApplication.instance()
        if theme_id == 2 or (theme_id == 0 and _os_prefers_dark()):
            # Dark
            palette = QPalette()
            palette.setColor(QPalette.ColorRole.Window, QColor(43, 43, 43))
            palette.setColor(QPalette.ColorRole.WindowText, QColor(220, 220, 220))
            palette.setColor(QPalette.ColorRole.Base, QColor(30, 30, 30))
            palette.setColor(QPalette.ColorRole.AlternateBase, QColor(50, 50, 50))
            palette.setColor(QPalette.ColorRole.Text, QColor(220, 220, 220))
            palette.setColor(QPalette.ColorRole.Button, QColor(53, 53, 53))
            palette.setColor(QPalette.ColorRole.ButtonText, QColor(220, 220, 220))
            palette.setColor(QPalette.ColorRole.BrightText, QColor(255, 255, 255))
            palette.setColor(QPalette.ColorRole.Link, QColor(86, 164, 255))
            palette.setColor(QPalette.ColorRole.Highlight, QColor(42, 130, 218))
            palette.setColor(QPalette.ColorRole.HighlightedText, QColor(255, 255, 255))
            palette.setColor(QPalette.ColorRole.ToolTipBase, QColor(50, 50, 50))
            palette.setColor(QPalette.ColorRole.ToolTipText, QColor(220, 220, 220))
            palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text, QColor(128, 128, 128))
            palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.ButtonText, QColor(128, 128, 128))
            app.setPalette(palette)
        else:
            # Light (system default)
            app.setPalette(app.style().standardPalette())

    # ─── Tree Selection ───────────────────────────────────────────────

    def _on_tree_click(self, item, column):
        data = item.data(0, Qt.ItemDataRole.UserRole)
        if data is None:
            return

        if isinstance(data, str):
            # Dataset-level click
            ds = DATASETS[data]
            self._show_dataset_info(data, ds)
        elif isinstance(data, tuple):
            # Run-level click
            ds_key, run_name = data
            ds = DATASETS[ds_key]
            self._show_run_info(ds_key, ds, run_name)

    def _show_dataset_info(self, ds_key, ds):
        self.info_title.setText(ds["title"])
        self.info_desc.setText(ds["description"])
        self.info_geology.setText(ds.get("geology_note", ""))

        wells_path = ds["wells"]
        if wells_path.exists():
            wl = WellList(str(wells_path))
            well_names = [w.name for w in wl.wells]
            data_names = list(wl.wells[0].data.keys()) if wl.wells else []
            region_names = list(wl.wells[0].region.keys()) if wl.wells else []
            self.info_wells.setText(
                f"Wells ({len(well_names)}): {', '.join(well_names)}\n"
                f"Data: {', '.join(data_names)}\n"
                f"Regions: {', '.join(region_names) if region_names else '(none)'}"
            )
        else:
            self.info_wells.setText(f"Wells file not found: {wells_path}")

        self._populate_params(ds, None)
        self._populate_ai_settings(ds)
        self.tabs.setCurrentIndex(0)

    def _show_run_info(self, ds_key, ds, run_name):
        run = next(r for r in ds["runs"] if r["name"] == run_name)
        self.info_title.setText(f"{ds['title']} → {run_name}")
        self.info_desc.setText(ds["description"])
        self.info_geology.setText(ds.get("geology_note", ""))

        wells_path = ds["wells"]
        if wells_path.exists():
            wl = WellList(str(wells_path))
            well_names = [w.name for w in wl.wells]
            self.info_wells.setText(f"Wells: {', '.join(well_names)}")
        else:
            self.info_wells.setText("")

        self._populate_params(ds, run)
        self._populate_ai_settings(ds)
        self.tabs.setCurrentIndex(0)

    def _populate_ai_settings(self, ds):
        """Set AI checkboxes from per-demo settings."""
        ai = {**AI_DEFAULTS, **ds.get("ai", {})}
        self.ai_quality_cb.setChecked(ai["quality"])
        self.ai_anomaly_cb.setChecked(ai["anomaly"])
        self.ai_uncertainty_cb.setChecked(ai["uncertainty"])
        self.ai_logqc_cb.setChecked(ai["log_qc"])

    def _populate_params(self, ds, run):
        """Show editable parameters for the selected dataset/run."""
        # Clear existing
        layout = self.param_group.layout()
        while layout.count():
            child = layout.takeAt(0)
            if child.widget():
                child.widget().deleteLater()
        self.param_widgets.clear()

        # Merge common + run opts
        opts = dict(ds.get("common_opts", {}))
        if run:
            opts.update(run.get("opts", {}))

        # Create widgets for each param
        for key, val in sorted(opts.items()):
            if key in ("cost_function", "debug_cor_info", "out_file"):
                continue  # skip internal ones

            if isinstance(val, float):
                w = QDoubleSpinBox()
                w.setRange(-100.0, 1000.0)
                w.setDecimals(2)
                w.setSingleStep(0.1)
                w.setValue(val)
            elif isinstance(val, int):
                w = QSpinBox()
                w.setRange(0, 10000)
                w.setValue(val)
            elif key == "order":
                w = QComboBox()
                w.addItems(["linear", "pyramidal", "position", "distality", "inverse"])
                w.setCurrentText(str(val))
            else:
                w = QLineEdit(str(val))

            label = key.replace("_", " ").title()
            if key in PARAM_HELP:
                w.setToolTip(PARAM_HELP[key])
            layout.addRow(label + ":", w)
            self.param_widgets[key] = w

    def _get_current_opts(self, ds, run):
        """Read current parameter values from widgets."""
        opts = dict(ds.get("common_opts", {}))
        if run:
            opts.update(run.get("opts", {}))

        # Override from widgets
        for key, widget in self.param_widgets.items():
            if isinstance(widget, QDoubleSpinBox):
                opts[key] = widget.value()
            elif isinstance(widget, QSpinBox):
                opts[key] = widget.value()
            elif isinstance(widget, QComboBox):
                opts[key] = widget.currentText()
            elif isinstance(widget, QLineEdit):
                text = widget.text()
                # Try to interpret as number
                try:
                    opts[key] = float(text)
                except ValueError:
                    opts[key] = text

        opts["debug_cor_info"] = 1
        return opts

    # ─── Running ──────────────────────────────────────────────────────

    def _get_selected_runs(self):
        """Return list of (ds_key, run_dict) for current selection."""
        items = self.tree.selectedItems()
        runs = []
        for item in items:
            data = item.data(0, Qt.ItemDataRole.UserRole)
            if isinstance(data, str):
                # Dataset selected — run all its runs
                ds = DATASETS[data]
                for r in ds["runs"]:
                    runs.append((data, r))
            elif isinstance(data, tuple):
                ds_key, run_name = data
                ds = DATASETS[ds_key]
                r = next(x for x in ds["runs"] if x["name"] == run_name)
                runs.append((ds_key, r))
        return runs

    def _run_selected(self):
        runs = self._get_selected_runs()
        if not runs:
            self.statusBar().showMessage("Nothing selected — click a dataset or run first")
            return
        self._execute_runs(runs)

    def _run_all(self):
        runs = []
        for ds_key, ds in DATASETS.items():
            for r in ds["runs"]:
                runs.append((ds_key, r))
        self._execute_runs(runs)

    def _execute_runs(self, runs):
        """Queue and execute a list of (ds_key, run) tuples."""
        self._pending_runs = list(runs)
        self._total_runs = len(runs)
        self._completed_runs = 0
        self._all_results = []
        self._run_results = {}

        # Clear old results display
        self._clear_plots()
        self.run_selector.clear()
        self.ranking_label.setText("")
        self.result_cost_label.setText("")
        self.log_text.clear()

        self.progress.setVisible(True)
        self.progress.setMaximum(self._total_runs)
        self.progress.setValue(0)
        self.btn_run_selected.setEnabled(False)
        self.btn_run_all.setEnabled(False)

        self._run_next()

    def _run_next(self):
        if not self._pending_runs:
            self._on_all_done()
            return

        ds_key, run = self._pending_runs.pop(0)
        ds = DATASETS[ds_key]

        # Get options (use widget values if this is the currently-displayed run)
        opts = dict(ds.get("common_opts", {}))
        opts.update(run.get("opts", {}))
        opts["debug_cor_info"] = 1

        # Try to use widget overrides if they match
        if self.param_widgets:
            for key, widget in self.param_widgets.items():
                if key in opts:
                    if isinstance(widget, QDoubleSpinBox):
                        opts[key] = widget.value()
                    elif isinstance(widget, QSpinBox):
                        opts[key] = widget.value()
                    elif isinstance(widget, QComboBox):
                        opts[key] = widget.currentText()
                    elif isinstance(widget, QLineEdit):
                        text = widget.text()
                        try:
                            opts[key] = float(text)
                        except ValueError:
                            opts[key] = text

        run_label = f"{ds['title']} / {run['name']}"
        self.statusBar().showMessage(f"Running: {run_label}...")
        self.log_text.append(f"\n{'─'*60}\n  Running: {run_label}\n{'─'*60}\n")

        worker = CorrelationWorker(run_label, ds["wells"], opts)
        worker.finished.connect(lambda name, rf, wl, log: self._on_run_finished(name, rf, wl, log))
        self._workers.append(worker)
        worker.start()

    def _on_run_finished(self, run_name, res_file, well_list, log_text):
        self._completed_runs += 1
        self.progress.setValue(self._completed_runs)

        # Append log
        self.log_text.append(log_text)
        self.log_text.moveCursor(QTextCursor.MoveOperation.End)

        if res_file is not None and res_file.get_nbr_results() > 0:
            cost = res_file.get_result_cost(0)
            n_cor = res_file.get_nbr_results()
            self.log_text.append(
                f"  ✓ {run_name}: cost={cost:.4f}, {n_cor} correlations\n")

            # Store result for paging
            self._run_results[run_name] = (res_file, well_list, n_cor)
            self.run_selector.addItem(f"{run_name}  (cost={cost:.4f}, n={n_cor})", run_name)

            # Track last result for RDDMS export
            self._last_res_file = res_file
            self._last_well_list = well_list
            self._rddms_export_btn.setEnabled(True)

            # Populate well list widget (first run sets it)
            if self._well_list_widget.count() == 0:
                self._populate_well_list(well_list)

            self._all_results.append((run_name, cost, n_cor))
        else:
            self.log_text.append(f"  ✗ {run_name}: FAILED\n")

        # Proceed to next
        QTimer.singleShot(50, self._run_next)

    def _on_all_done(self):
        self.progress.setVisible(False)
        self.btn_run_selected.setEnabled(True)
        self.btn_run_all.setEnabled(True)

        n = len(self._all_results)
        self.statusBar().showMessage(f"Done — {n} run(s) completed successfully")
        self.tabs.setCurrentIndex(1)  # Switch to Results tab

        # Auto-select first run to display
        if self.run_selector.count() > 0:
            self.run_selector.setCurrentIndex(0)
            self._display_current_result()

    def _format_summary(self):
        lines = ["\n─── Summary ───────────────────────────────────────\n"]
        for name, cost, n_cor in self._all_results:
            lines.append(f"  {name:40s}  cost={cost:.4f}  ({n_cor} cors)")
        return "\n".join(lines)

    # ─── Result Paging ────────────────────────────────────────────────

    def _on_run_selector_changed(self, index):
        """User selected a different run from the dropdown."""
        if index < 0:
            return
        run_name = self.run_selector.itemData(index)
        if run_name and run_name in self._run_results:
            res_file, well_list, n_cor = self._run_results[run_name]
            self.result_spin.blockSignals(True)
            self.result_spin.setMaximum(n_cor - 1)
            self.result_spin.setValue(0)
            self.result_spin.blockSignals(False)
            self._display_current_result()

    def _page_prev(self):
        val = self.result_spin.value()
        if val > 0:
            self.result_spin.setValue(val - 1)

    def _page_next(self):
        val = self.result_spin.value()
        if val < self.result_spin.maximum():
            self.result_spin.setValue(val + 1)

    # ─── Well Selection / Ordering ────────────────────────────────────

    def _populate_well_list(self, well_list):
        """Fill the well list widget from a WellList."""
        self._well_list_widget.clear()
        for w in well_list.wells:
            item = QListWidgetItem(w.name)
            item.setSelected(True)
            self._well_list_widget.addItem(item)

    def _select_all_wells(self):
        for i in range(self._well_list_widget.count()):
            self._well_list_widget.item(i).setSelected(True)

    def _select_no_wells(self):
        self._well_list_widget.clearSelection()

    def _move_well_up(self):
        row = self._well_list_widget.currentRow()
        if row > 0:
            item = self._well_list_widget.takeItem(row)
            self._well_list_widget.insertItem(row - 1, item)
            self._well_list_widget.setCurrentRow(row - 1)

    def _move_well_down(self):
        row = self._well_list_widget.currentRow()
        if row < self._well_list_widget.count() - 1:
            item = self._well_list_widget.takeItem(row)
            self._well_list_widget.insertItem(row + 1, item)
            self._well_list_widget.setCurrentRow(row + 1)

    def _apply_well_selection(self):
        """Re-display using only selected wells in the current order."""
        self._display_current_result()

    def _get_visible_wells(self):
        """Return list of selected well names in current order."""
        names = []
        for i in range(self._well_list_widget.count()):
            item = self._well_list_widget.item(i)
            if item.isSelected():
                names.append(item.text())
        return names if names else None

    def _on_result_spin_changed(self, value):
        self._display_current_result()

    def _display_current_result(self):
        """Render the plot for the currently selected run + result index."""
        index = self.run_selector.currentIndex()
        if index < 0:
            return
        run_name = self.run_selector.itemData(index)
        if not run_name or run_name not in self._run_results:
            return

        res_file, well_list, n_cor = self._run_results[run_name]
        cor_index = self.result_spin.value()

        # Update cost label
        cost = res_file.get_result_cost(cor_index)
        self.result_cost_label.setText(
            f"Cost: {cost:.4f}  |  Result {cor_index + 1} of {n_cor}")

        # Update ranking info
        ranking_lines = []
        for i in range(min(n_cor, 10)):
            c = res_file.get_result_cost(i)
            marker = " ◀" if i == cor_index else ""
            ranking_lines.append(f"  #{i}: cost={c:.4f}{marker}")
        self.ranking_label.setText(
            f"Top-{min(n_cor, 10)} ranked results (lowest cost = best):\n"
            + "\n".join(ranking_lines))

        # Generate plot for this cor_index
        title = f"{run_name} — Result #{cor_index}"
        plot_settings = self._get_plot_colors()
        plot_path = generate_plot(well_list, res_file, title, cor_index=cor_index,
                                  plot_settings=plot_settings)

        # Display in plot area
        self._clear_plots()
        if plot_path:
            self._add_plot(title, plot_path, cost, n_cor)

        # AI post-processing
        self._run_ai_analysis(res_file, well_list, cor_index, n_cor)

    # ─── AI Post-Processing ───────────────────────────────────────────

    def _run_ai_analysis(self, res_file, well_list, cor_index, n_cor):
        """Run enabled AI features and append results to ranking label."""
        ai_lines = []

        try:
            if self.ai_quality_cb.isChecked():
                from weco.ai.quality import CorrelationQuality
                cq = CorrelationQuality(res_file, well_list)
                scores = cq.score_all()
                if cor_index < len(scores):
                    s = scores[cor_index]
                    ai_lines.append(
                        f"  ★ Quality: {s.overall:.2f}  "
                        f"(cost={s.cost_score:.2f}, gaps={s.gap_score:.2f}, "
                        f"sim={s.similarity_score:.2f})")

            if self.ai_anomaly_cb.isChecked():
                from weco.ai.anomaly import CorrelationAnomalyDetector
                det = CorrelationAnomalyDetector(res_file, well_list)
                flags = det.flag(cor_index)
                n_flagged = sum(1 for f in flags if f.is_anomaly)
                if n_flagged > 0:
                    ai_lines.append(
                        f"  ⚠ Anomaly: {n_flagged} suspicious line(s) flagged")
                else:
                    ai_lines.append("  ✓ Anomaly: no suspicious lines detected")

            if self.ai_uncertainty_cb.isChecked() and n_cor > 1:
                from weco.ai.uncertainty import CorrelationUncertainty
                cu = CorrelationUncertainty(res_file, well_list)
                summary = cu.summary(top_n=min(n_cor, 10))
                ai_lines.append(
                    f"  ↔ Uncertainty: mean spread={summary.mean_spread:.2f}, "
                    f"max={summary.max_spread:.2f}")
        except Exception as e:
            ai_lines.append(f"  [AI error: {e}]")

        if ai_lines:
            current = self.ranking_label.text()
            self.ranking_label.setText(
                current + "\n\n── AI Analysis ──\n" + "\n".join(ai_lines))

    # ─── Plot Display ─────────────────────────────────────────────────

    def _clear_plots(self):
        while self.plot_layout.count():
            child = self.plot_layout.takeAt(0)
            if child.widget():
                child.widget().deleteLater()

    def _add_plot(self, title, png_path, cost, n_cor):
        """Add a plot image to the results tab."""
        frame = QFrame()
        frame.setFrameShape(QFrame.Shape.StyledPanel)
        frame_layout = QVBoxLayout(frame)
        frame_layout.setContentsMargins(4, 4, 4, 4)

        # Image
        pixmap = QPixmap(png_path)
        if not pixmap.isNull():
            # Scale to fit width
            scaled = pixmap.scaledToWidth(
                min(900, self.width() - 350),
                Qt.TransformationMode.SmoothTransformation
            )
            img_label = QLabel()
            img_label.setPixmap(scaled)
            img_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            frame_layout.addWidget(img_label)
        else:
            frame_layout.addWidget(QLabel(f"(plot saved to {png_path})"))

        self.plot_layout.addWidget(frame)

    # ─── Plot Settings (color pickers) ─────────────────────────────────

    def _pick_log_color(self, idx):
        """Open color dialog for log trace color."""
        color = QColorDialog.getColor(QColor(LOG_COLORS[idx]), self, f"Log {idx+1} Color")
        if color.isValid():
            LOG_COLORS[idx] = color.name()
            self._log_color_btns[idx].setStyleSheet(
                f"background-color: {color.name()}; color: white; font-size: 9px;")
            self._display_current_result()

    def _pick_line_color(self, line_type):
        """Open color dialog for correlation line colors."""
        if line_type == 'boundary':
            current = self._boundary_color_btn.styleSheet()
            color = QColorDialog.getColor(QColor('#D32F2F'), self, "Boundary Line Color")
            if color.isValid():
                self._boundary_color = color.name()
                self._boundary_color_btn.setStyleSheet(
                    f"background-color: {color.name()}; color: white; font-size: 9px;")
        elif line_type == 'gap':
            color = QColorDialog.getColor(QColor('#1565C0'), self, "Gap/Hiatus Line Color")
            if color.isValid():
                self._gap_color = color.name()
                self._gap_color_btn.setStyleSheet(
                    f"background-color: {color.name()}; color: white; font-size: 9px;")
        elif line_type == 'framework':
            color = QColorDialog.getColor(QColor('#999999'), self, "Framework Line Color")
            if color.isValid():
                self._framework_color = color.name()
                self._framework_color_btn.setStyleSheet(
                    f"background-color: {color.name()}; font-size: 9px;")
        self._display_current_result()

    def _get_plot_colors(self):
        """Return current color settings for generate_plot."""
        return {
            'boundary': getattr(self, '_boundary_color', '#D32F2F'),
            'gap': getattr(self, '_gap_color', '#1565C0'),
            'framework': getattr(self, '_framework_color', '#999999'),
            'max_boundaries': self._max_boundaries_spin.value(),
            'max_gaps': self._max_gaps_spin.value(),
        }

    # ─── Export ───────────────────────────────────────────────────────

    def _export_results(self):
        """Legacy stub — use _export_to_rddms() instead."""
        self._export_to_rddms()

    def _export_to_rddms(self):
        """Export current correlation results to RDDMS dataspace.

        Uses the same weco.rddms functions as the web client's
        POST /rddms/export-results route.
        """
        if not hasattr(self, '_last_res_file') or self._last_res_file is None:
            self._rddms_status_label.setText("No results to export. Run a correlation first.")
            return
        if not hasattr(self, '_last_well_list') or self._last_well_list is None:
            self._rddms_status_label.setText("No well data loaded.")
            return

        url = self._rddms_url_edit.text().strip()
        dataspace = self._rddms_dataspace_edit.text().strip()
        if not url:
            self._rddms_status_label.setText("Enter an RDDMS URL.")
            return
        if not dataspace:
            self._rddms_status_label.setText("Enter a dataspace name.")
            return

        cor_num = self._rddms_cor_num_spin.value()
        export_markers = self._rddms_markers_cb.isChecked()
        export_zonation = self._rddms_zonation_cb.isChecked()
        export_strat = self._rddms_strat_cb.isChecked()

        if not (export_markers or export_zonation or export_strat):
            self._rddms_status_label.setText("Select at least one export type.")
            return

        self._rddms_status_label.setText("Exporting...")
        self._rddms_export_btn.setEnabled(False)
        QApplication.processEvents()

        try:
            from weco.osdu_auth import get_token
            token = get_token()
        except Exception as e:
            self._rddms_status_label.setText(f"Auth failed: {e}")
            self._rddms_export_btn.setEnabled(True)
            return

        total = 0
        detail_parts = []

        try:
            if export_markers:
                from weco.rddms import rddms_export_markers
                nm = rddms_export_markers(
                    url, token, dataspace,
                    self._last_res_file, self._last_well_list,
                    cor_num=cor_num,
                )
                total += nm
                detail_parts.append(f"{nm} markers")

            if export_zonation:
                from weco.rddms import rddms_export_zonation
                nz = rddms_export_zonation(
                    url, token, dataspace,
                    self._last_res_file, self._last_well_list,
                    cor_num=cor_num,
                )
                total += nz
                detail_parts.append(f"{nz} zone logs")

            if export_strat:
                from weco.rddms import rddms_export_strat_column
                ns = rddms_export_strat_column(
                    url, token, dataspace,
                    self._last_res_file, self._last_well_list,
                    cor_num=cor_num,
                )
                total += ns
                detail_parts.append("strat column")

            self._rddms_status_label.setText(
                f"✓ Exported {total} objects to '{dataspace}' "
                f"(realisation {cor_num}): {', '.join(detail_parts)}"
            )
        except ImportError as e:
            self._rddms_status_label.setText(f"RESQML package not available: {e}")
        except Exception as e:
            self._rddms_status_label.setText(f"Export failed: {e}")
        finally:
            self._rddms_export_btn.setEnabled(True)


# ═══════════════════════════════════════════════════════════════════════════
#  Entry Point
# ═══════════════════════════════════════════════════════════════════════════

def main():
    app = QApplication(sys.argv)
    app.setApplicationName("WeCo Demo Runner")
    app.setStyle("Fusion")

    # Follow OS light/dark mode
    from PyQt6.QtGui import QPalette
    if _os_prefers_dark():
        palette = QPalette()
        palette.setColor(QPalette.ColorRole.Window, QColor(43, 43, 43))
        palette.setColor(QPalette.ColorRole.WindowText, QColor(220, 220, 220))
        palette.setColor(QPalette.ColorRole.Base, QColor(30, 30, 30))
        palette.setColor(QPalette.ColorRole.AlternateBase, QColor(50, 50, 50))
        palette.setColor(QPalette.ColorRole.Text, QColor(220, 220, 220))
        palette.setColor(QPalette.ColorRole.Button, QColor(53, 53, 53))
        palette.setColor(QPalette.ColorRole.ButtonText, QColor(220, 220, 220))
        palette.setColor(QPalette.ColorRole.BrightText, QColor(255, 255, 255))
        palette.setColor(QPalette.ColorRole.Link, QColor(86, 164, 255))
        palette.setColor(QPalette.ColorRole.Highlight, QColor(42, 130, 218))
        palette.setColor(QPalette.ColorRole.HighlightedText, QColor(255, 255, 255))
        palette.setColor(QPalette.ColorRole.ToolTipBase, QColor(50, 50, 50))
        palette.setColor(QPalette.ColorRole.ToolTipText, QColor(220, 220, 220))
        palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text, QColor(128, 128, 128))
        palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.ButtonText, QColor(128, 128, 128))
        app.setPalette(palette)

    window = DemoRunnerWindow()
    window.show()
    sys.exit(app.exec())


def _os_prefers_dark() -> bool:
    """Detect OS dark mode preference."""
    # Qt 6.5+ exposes colorScheme directly
    try:
        from PyQt6.QtCore import Qt as _Qt
        scheme = QApplication.styleHints().colorScheme()
        if scheme == _Qt.ColorScheme.Dark:
            return True
        if scheme == _Qt.ColorScheme.Light:
            return False
    except (AttributeError, TypeError):
        pass
    # Fallback: check GTK/GNOME/KDE settings on Linux
    import subprocess
    try:
        out = subprocess.check_output(
            ["gsettings", "get", "org.gnome.desktop.interface", "color-scheme"],
            stderr=subprocess.DEVNULL, text=True,
        ).strip().strip("'")
        if "dark" in out:
            return True
    except (FileNotFoundError, subprocess.CalledProcessError):
        pass
    try:
        out = subprocess.check_output(
            ["gsettings", "get", "org.gnome.desktop.interface", "gtk-theme"],
            stderr=subprocess.DEVNULL, text=True,
        ).strip().strip("'")
        if "dark" in out.lower():
            return True
    except (FileNotFoundError, subprocess.CalledProcessError):
        pass
    return False


if __name__ == "__main__":
    main()
