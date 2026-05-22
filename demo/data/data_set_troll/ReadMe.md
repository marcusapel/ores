# Troll Field Dataset

## Source
Sognefjord Formation (Upper Jurassic), Troll field, North Sea (blocks 31/2, 31/3, 31/5, 31/6).
Extracted from core-based sedimentological interpretation spreadsheet.

## Wells: 23
31_2-1, 31_2-12, 31_2-14, 31_2-17S, 31_2-17SA, 31_2-2, 31_2-22S, 31_2-3, 31_2-6, 31_2-8, 31_2-9, 31_3-1, 31_3-2, 31_5-2, 31_5-3, 31_5-4S, 31_6-1, 31_6-2, 31_6-3, 31_6-5, 31_6-6, 31_6-8, 32_4-1

## Data Channels
- **MD**: Measured depth (m)
- **FACIES**: Depositional environment (Facies10 scheme, 1-10)
- **DISTALITY**: Proximal-distal position (1=distal/shelf ... 4=proximal/channel)
- **BIOZONE**: Biostratigraphic zonation (chronostratigraphic constraint)
- **SEQUENCE**: Depositional sequence (Series 2-7)

## Facies Legend (Facies10)
| Code | Environment | Distality |
|------|-------------|-----------|
| 1 | Distributary channel | 4 (proximal) |
| 2 | Foreshore / Tidal channel | 4 (proximal) |
| 3 | Tidal bar / Mixed flat | 3 (intermediate) |
| 4 | Subtidal flat | 3 (intermediate) |
| 5 | Floodplain lake | 2 (intermediate) |
| 6 | Mouth bar | 2 (intermediate) |
| 7 | Upper delta front | 2 (intermediate) |
| 8 | Lower delta front | 2 (distal) |
| 9 | Prodelta | 1 (distal) |
| 10 | Shelf / Offshore | 1 (distal) |

## Correlation Strategy
1. **options_distal.txt**: Distal CCF — uses facies + distality (Walther's Law)
2. **options_sequence.txt**: Variance + same-region (SEQUENCE boundaries)
3. **options_basic.txt**: Unconstrained variance (baseline comparison)

All constrained by BIOZONE no-crossing (where available).

## Geological Context
The Sognefjord Formation is a wave/tide-influenced deltaic system prograding
northward. Wells span proximal (distributary channels, foreshores) to distal
(prodelta, shelf) environments. Sequences 2-7 represent major regressive-
transgressive cycles bounded by flooding surfaces.
