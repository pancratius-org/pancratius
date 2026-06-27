# E1 working-half readout — e1-instrument-working (n=747)

det: {'prose': 105, 'lineated': 642}   truth source: {'gate': 726, 'human': 21}

## How wrong is the importer, by direction
P(truth=lineated | det=prose)  [the weak side] : 50/105 = 0.476  |  gate 46/100 = 0.460  human 4/5 = 0.800  |  en 16/32 = 0.500  ru 34/73 = 0.466
P(truth=prose | det=lineated)  [assumed free]  : 14/642 = 0.022  |  gate 13/626 = 0.021  human 1/16 = 0.062  |  en 9/210 = 0.043  ru 5/432 = 0.012

det⊕truth disagreement (E2 router target): 64 (50 verse-missed + 14 over-lineated)

## Caveats
- working half only; frozen scored once in E4
- gate truth = panel verdict (not independent); only human is ground truth
- no student posterior — det/truth rates only
