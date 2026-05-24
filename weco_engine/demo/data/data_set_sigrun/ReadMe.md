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

## Validation

The reference correlation panel (`tmp/Correlation_panel_1_500_sigrun_wells.pdf`)
shows the published correlation of Hugin flooding surfaces from Equinor's internal
interpretation. The WeCo n-best output should include this stated correlation as
one of the diverse solutions.

## References

- Knaust, D. & Hoth, S. (2021) Depositional environment and reservoir quality
  of the Hugin Formation, Gudrun–Sigrun area, South Viking Graben. *Marine and
  Petroleum Geology* 133, 105236.
- Cole, J.M. et al. (2008) The Hugin Formation: Reservoir geology and
  palaeogeography in the Gudrun–Sigrun area, Norwegian North Sea. *Petrol.
  Geol. Conf. Proceedings* 7, 689–699.
- Husmo, T. et al. (2003) Lower and Middle Jurassic. In: Evans, D. et al.
  (eds) *The Millennium Atlas: Petroleum Geology of the Central and Northern
  North Sea*, Geological Society, London, 129–156.
- Boyd, R. et al. (2006) Estuarine and incised valley facies models. In:
  *Facies Models Revisited*, SEPM Special Publication 84, 171–234.
- Baville, P. (2022) *Stratigraphic correlation of well logs using
  graph-based dynamic time warping*, PhD Thesis, Université de Lorraine.
- Baville, P. et al. (2022) Computer-assisted stochastic multi-well
  correlation: Sedimentary facies versus well distality. *Marine and
  Petroleum Geology* 135, 105371.

## Authors

Well data courtesy of Equinor ASA / DISKOS national well database.
Facies interpretation: D. Knaust (Equinor).
WeCo integration: ASGA/RING, Université de Lorraine.
