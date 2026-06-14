# E1 working-half readout — e1-instrument-working (n=747)

det: {'prose': 102, 'lineated': 645}   truth source: {'gate': 726, 'human': 21}

## How wrong is the importer, by direction
P(truth=lineated | det=prose)  [the weak side] : 47/102 = 0.461  |  gate 43/97 = 0.443  human 4/5 = 0.800  |  en 16/32 = 0.500  ru 31/70 = 0.443
P(truth=prose | det=lineated)  [assumed free]  : 14/645 = 0.022  |  gate 13/629 = 0.021  human 1/16 = 0.062  |  en 9/210 = 0.043  ru 5/435 = 0.011

det⊕truth disagreement (E2 router target): 61 (47 verse-missed + 14 over-lineated)

## Caveats
- working half only; frozen scored once in E4
- gate truth = panel verdict (not independent); only human is ground truth
- no student posterior — det/truth rates only
