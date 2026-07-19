# E1 working-half readout — e1-v2-working (n=728)

det: {'lineated': 638, 'prose': 90}   truth source: {'gate': 708, 'human': 20}

## How wrong is the importer, by direction
P(truth=lineated | det=prose)  [the weak side] : 40/90 = 0.444  |  gate 36/85 = 0.424  human 4/5 = 0.800  |  en 12/25 = 0.480  ru 28/65 = 0.431
P(truth=prose | det=lineated)  [assumed free]  : 16/638 = 0.025  |  gate 15/623 = 0.024  human 1/15 = 0.067  |  en 10/199 = 0.050  ru 6/439 = 0.014

det⊕truth disagreement (E2 router target): 56 (40 verse-missed + 16 over-lineated)

## Caveats
- working half only; frozen scored once in E4
- gate truth = panel verdict (not independent); only human is ground truth
- no student posterior — det/truth rates only
