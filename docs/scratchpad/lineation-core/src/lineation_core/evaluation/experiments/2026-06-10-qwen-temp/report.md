# 2026-06-10-qwen-temp

**Question.** Are the qwens effectively 0.0-tuned (do they degrade in accuracy/stability from 0.0 to 0.5)?

Sweep axis: `temperature` over ['0.0', '0.5']; eval set `reader_bench`; git `a127402ecc9ea809a95366f6c5f7522fcbfd3e25+dirty`.

| point | reader | modality | balAcc | prose | lin | cover | faults | instab | trunc | $/1k |
|---|---|---|---|---|---|---|---|---|---|---|
| 0.0 | qwen-235b | text | 0.922 | 0.911 | 0.933 | 1.000 | clean | 0.09 | 4 | 0.1474 |
| 0.0 | qwen-flash | text | 0.842 | 0.856 | 0.828 | 0.930 | missing_key:19 unmapped_key:961 | 0.07 | 8 | 4.4201 |
| 0.5 | qwen-235b | text | 0.883 | 0.911 | 0.856 | 1.000 | unmapped_key:72 | 0.06 | 2 | 0.1241 |
| 0.5 | qwen-flash | text | 0.869 | 0.933 | 0.806 | 0.933 | key_item_mismatch:1 missing_key:18 unmapped_key:1535 | 0.08 | 3 | 4.1126 |
