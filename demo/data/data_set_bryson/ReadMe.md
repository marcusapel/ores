# Bryson Canyon Dataset

## Source
Neslen Formation (Upper Cretaceous), Bryson Canyon, Book Cliffs, Utah.
Coastal plain to estuarine deposits — coal-bearing sequences.
Exported from IC (Integrated Correlation) software as discrete facies logs.

## Wells: 7
B10, B11, B3, B5, B6, B7, B9

## Data Channels
- **MD**: Measured depth (m, positive downward, converted from TVD-SS)
- **FACIES**: Depositional facies (1-8)
- **DISTALITY**: Proximal-distal position derived from facies (1=proximal, 4=distal)
- **ZONE**: Reservoir zonation / sedimentary units (1-6)
- **SEQSTRAT**: Sequence stratigraphy boundaries (4th/5th order)

## Facies Legend
| Code | Name | Distality | Description |
|------|------|-----------|-------------|
| 1 | Coal | 1 (prox.) | Peat-forming swamp |
| 2 | Marsh | 1 (prox.) | Coastal marsh/swamp |
| 3 | Wave-influenced bayfill | 3 (dist.) | Wave-worked estuarine bay |
| 4 | Sub-bay | 4 (dist.) | Central bay/lagoon |
| 5 | Bayhead delta | 2 (int.) | Progradational bay-fill sand |
| 6 | Crevasse splay | 2 (int.) | Overbank sand sheets |
| 7 | Distributary channel | 1 (prox.) | Channel belt sand bodies |
| 8 | Lagoonal mud | 4 (dist.) | Quiet-water bay/lagoon mud |

## Sequence Stratigraphy
| Code | Name | Order |
|------|------|-------|
| 1 | Cozzette-SB | 4th |
| 2 | Buck MFS3 | 5th |
| 3 | Buck FS2 | 5th |
| 4 | Buck FS1 | 5th |
| 5 | Buck-SB | 4th |
| 6 | Corcoran FS3 | 5th |
| 7 | Corcoran MFS2 | 5th |
| 8 | Corcoran FS1 | 5th |
| 9 | Corcoran-SB | 4th |

## Correlation Strategy
1. **options_distal.txt**: Distal CCF — facies + distality (Walther's Law)
2. **options_seqstrat.txt**: Variance + SEQSTRAT same-region constraint
3. **options_basic.txt**: Unconstrained (shows ambiguity from coal seam repetition)

## Geological Context
The Neslen Formation represents tide/wave-influenced coastal-plain to estuarine
deposits. Coal seams form excellent local markers but split/merge laterally.
The challenge: repeated Coal-Marsh-Bayfill cyclicity creates ambiguity in
well-to-well correlation. Reservoir zonation (units 1-6) and 4th/5th-order
sequence boundaries constrain valid correlations.
