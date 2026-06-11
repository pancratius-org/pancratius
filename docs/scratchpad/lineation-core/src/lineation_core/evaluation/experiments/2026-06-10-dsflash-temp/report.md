# 2026-06-10-dsflash-temp

**Question.** Does ds-flash get less stable / less accurate as temperature rises (is it effectively 0.0-tuned)?

Sweep axis: `temperature` over ['0.0', '0.3', '0.5', '0.7']; eval set `reader_bench`; git `b780d9ec8f1e2ac754425d405690e19299eb3471+dirty`.

| point | reader | modality | balAcc | prose | lin | cover | faults | instab | trunc | $/1k |
|---|---|---|---|---|---|---|---|---|---|---|
| 0.0 | ds-flash | text | 0.883 | 0.911 | 0.856 | 1.000 | clean | 0.11 | 2 | 0.2723 |
| 0.3 | ds-flash | text | 0.917 | 0.944 | 0.889 | 1.000 | dup_key:12 | 0.19 | 0 | 0.4591 |
| 0.5 | ds-flash | text | 0.933 | 0.933 | 0.933 | 1.000 | clean | 0.16 | 0 | 0.4615 |
| 0.7 | ds-flash | text | 0.878 | 0.933 | 0.822 | 1.000 | clean | 0.10 | 1 | 0.4468 |
