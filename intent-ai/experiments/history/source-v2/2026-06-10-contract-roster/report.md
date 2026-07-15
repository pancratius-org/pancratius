# 2026-06-10-contract-roster

**Question.** Per reader, does json_keyed cut unmapped_key faults at no accuracy cost — and which readers regress — so we can assign a contract per reader?

Sweep axis: `contract` over ['json_array', 'json_keyed']; eval set `reader_bench`; git `a127402ecc9ea809a95366f6c5f7522fcbfd3e25+dirty`.

| point | reader | modality | balAcc | prose | lin | cover | faults | instab | trunc | $/1k |
|---|---|---|---|---|---|---|---|---|---|---|
| json_array | grok | text | 0.956 | 0.933 | 0.978 | 1.000 | clean | 0.03 | 0 | 2.5155 |
| json_array | gemini-lite | text | 0.939 | 0.944 | 0.933 | 1.000 | clean | 0.03 | 0 | 0.5386 |
| json_array | ds-flash | text | 0.911 | 0.911 | 0.911 | 1.000 | dup_key:2 | 0.11 | 2 | 0.2693 |
| json_array | qwen-235b | text | 0.892 | 0.900 | 0.883 | 0.996 | missing_key:1 unmapped_key:45 | 0.12 | 0 | 0.1373 |
| json_array | qwen-flash | text | 0.892 | 0.933 | 0.850 | 0.941 | key_item_mismatch:11 missing_key:16 unmapped_key:1233 | 0.07 | 3 | 4.2596 |
| json_array | grok-vision | vision | 0.922 | 0.911 | 0.933 | 1.000 | clean | 0.08 | 0 | 3.8452 |
| json_array | gemini-vision | vision | 0.931 | 0.989 | 0.872 | 1.000 | clean | 0.01 | 0 | 0.7395 |
| json_keyed | grok | text | 0.953 | 0.933 | 0.972 | 1.000 | clean | 0.01 | 0 | 2.8096 |
| json_keyed | gemini-lite | text | 0.944 | 0.944 | 0.944 | 1.000 | clean | 0.04 | 0 | 0.5651 |
| json_keyed | ds-flash | text | 0.928 | 0.944 | 0.911 | 1.000 | clean | 0.17 | 3 | 0.2212 |
| json_keyed | qwen-235b | text | 0.892 | 0.900 | 0.883 | 1.000 | clean | 0.11 | 2 | 0.1409 |
| json_keyed | qwen-flash | text | 0.889 | 0.944 | 0.833 | 0.933 | missing_key:18 unmapped_key:1473 | 0.04 | 3 | 4.2775 |
| json_keyed | grok-vision | vision | 0.958 | 0.944 | 0.972 | 1.000 | clean | 0.07 | 0 | 4.1326 |
| json_keyed | gemini-vision | vision | 0.725 | 0.589 | 0.861 | 1.000 | clean | 0.14 | 0 | 0.6897 |
