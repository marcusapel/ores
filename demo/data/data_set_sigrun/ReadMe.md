# Sigrun Field Dataset

## Source
Hugin Formation (Upper Jurassic), Sigrun field, block 15/3, North Sea.
Extracted from RMS LAS v3 exports + well picks.

## Wells: 6
15_3-1_S, 15_3-3, 15_3-4, 15_3-5, 15_3-7, 15_3-9_T2

## Data Channels
- **MD**: Measured depth (m)
- **GR**: Gamma ray log (LFP_GR, API units)
- **NPHI**: Total porosity (LFP_PHIT, v/v) — only in 15_3-4, 15_3-5
- **FACIES**: Genetic facies code (1-8) — only in 15_3-4, 15_3-5
- **SEQUENCE**: Parasequence bounded by Hugin flooding surfaces (1-10)
- **BIOZONE**: Biozone assignment at marker depths

## Flooding Surface Hierarchy (truth correlation horizons)
- **Hugin_FS_m** (Parasequence 1)
- **Hugin_FS_l** (Parasequence 2)
- **Hugin_FS_k** (Parasequence 3)
- **Hugin_FS_j** (Parasequence 4)
- **Hugin_FS_i** (Parasequence 5)
- **Hugin_FS_h** (Parasequence 6)
- **Hugin_FS_g** (Parasequence 7)
- **Hugin_FS_f** (Parasequence 8)
- **Hugin_FS_ E** (Parasequence 9)
- **TopSleipner** (Parasequence 10)

## Facies Legend
| Code | Environment | Distality |
|------|-------------|-----------|
| 1 | Tidal | 2 |
| 2 | Lagoon | 3 |
| 3 | Beach/Shoreface | 1 |
| 4 | Prodelta | 5 |
| 5 | Floodplain | 2 |
| 6 | Marsh | 2 |
| 7 | Crevasse splay | 2 |
| 8 | Channel | 1 |

## Correlation Strategy
1. **options_gr.txt**: GR variance + flooding surface no-crossing
2. **options_composite.txt**: GR+NPHI + no-crossing
3. **options_unconstrained.txt**: Unconstrained (uncertainty baseline)

## Geological Context
The Hugin Formation is a tide-dominated shallow marine system with lateral facies
changes from tidal channels/bars (proximal) to prodelta/offshore (distal). The
flooding surfaces (Hugin_FS_m through _f) provide isochronous correlation markers.
Wells are spaced 3-8 km apart across the field.
