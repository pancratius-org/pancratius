# 2026-06-10-contested-gate

**Question.** Does the legacy anchor-led gate (conf_floor=0.7, min_core_agree=2) hold on the contested hard population with the production roster?

Sweep axis: `contract` over ['json_keyed']; eval set `contested`; git `a127402ecc9ea809a95366f6c5f7522fcbfd3e25+dirty`.

| point | reader | modality | balAcc | prose | lin | cover | faults | instab | trunc | $/1k |
|---|---|---|---|---|---|---|---|---|---|---|
| json_keyed | grok-vision | vision | 0.877 | 0.942 | 0.811 | 0.998 | clean | 0.16 | 0 | 3.5126 |
| json_keyed | gemini-lite | text | 0.844 | 0.919 | 0.770 | 0.998 | clean | 0.09 | 0 | 0.4844 |
| json_keyed | ds-flash | text | 0.880 | 0.884 | 0.876 | 0.998 | clean | 0.08 | 1 | 0.1646 |
