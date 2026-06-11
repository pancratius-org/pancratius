# 2026-06-10-qwen235b-keyed-temp

**Question.** Does qwen-235b under json_keyed at 0.0 give both clean protocol and its ~0.922 accuracy (the corner that would qualify it)?

Sweep axis: `temperature` over ['0.0', '0.5']; eval set `reader_bench`; git `a127402ecc9ea809a95366f6c5f7522fcbfd3e25+dirty`.

| point | reader | modality | balAcc | prose | lin | cover | faults | instab | trunc | $/1k |
|---|---|---|---|---|---|---|---|---|---|---|
| 0.0 | qwen-235b | text | 0.889 | 0.900 | 0.878 | 1.000 | unmapped_key:16 | 0.07 | 2 | 0.1499 |
| 0.5 | qwen-235b | text | 0.900 | 0.933 | 0.867 | 1.000 | unmapped_key:18 | 0.13 | 6 | 0.1559 |
