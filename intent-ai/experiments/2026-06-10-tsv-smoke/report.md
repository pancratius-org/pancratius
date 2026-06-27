# 2026-06-10-tsv-smoke

**Question.** Does the schemaless TSV contract parse correctly against a live provider?

Sweep axis: `contract` over ['tsv']; eval set `reader_bench`; git `b780d9ec8f1e2ac754425d405690e19299eb3471+dirty`.

| point | reader | modality | balAcc | prose | lin | cover | faults | instab | trunc | $/1k |
|---|---|---|---|---|---|---|---|---|---|---|
| tsv | ds-flash | text | 0.658 | 0.478 | 0.839 | 0.748 | missing_key:68 unmapped_key:383 | 0.00 | 4 | 0.1425 |
