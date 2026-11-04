# Desempenho e overhead — aplicações reais

Médias entre repetições. Percentis são calculados por ensaio e depois agregados.

| API | Carga req/s | Modo | Média ms | p50 ms | p95 ms | p99 ms | Sucessos/s | HTTP 200 / total |
| --- | ---: | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| juice | 10 | direct | 21.383 | 21.927 | 26.128 | 42.928 | 10.00 | 90 / 90 |
| juice | 10 | proxy | 24.258 | 23.629 | 27.619 | 53.340 | 10.00 | 90 / 90 |
| juice | 10 | monitor | 24.968 | 25.346 | 29.338 | 30.629 | 10.00 | 90 / 90 |
| juice | 50 | direct | 14.012 | 12.358 | 18.107 | 71.828 | 50.00 | 450 / 450 |
| juice | 50 | proxy | 15.899 | 14.111 | 20.321 | 71.762 | 50.00 | 450 / 450 |
| juice | 50 | monitor | 16.849 | 14.769 | 21.374 | 90.733 | 50.00 | 450 / 450 |
| juice | 100 | direct | 430.069 | 443.356 | 639.867 | 647.180 | 84.29 | 900 / 900 |
| juice | 100 | proxy | 561.484 | 522.107 | 901.187 | 909.516 | 78.65 | 900 / 900 |
| juice | 100 | monitor | 539.014 | 510.760 | 886.015 | 960.238 | 79.66 | 900 / 900 |
| juice | 200 | direct | 1913.349 | 2063.741 | 3140.698 | 3225.771 | 96.96 | 1800 / 1800 |
| juice | 200 | proxy | 2149.596 | 2319.164 | 3575.359 | 3643.818 | 90.32 | 1800 / 1800 |
| juice | 200 | monitor | 2243.257 | 2467.896 | 3711.432 | 3783.972 | 88.74 | 1800 / 1800 |
| memos | 10 | direct | 2.406 | 2.333 | 3.281 | 5.094 | 10.00 | 90 / 90 |
| memos | 10 | proxy | 4.527 | 4.580 | 5.906 | 6.453 | 10.00 | 90 / 90 |
| memos | 10 | monitor | 6.387 | 6.604 | 8.441 | 8.881 | 10.00 | 90 / 90 |
| memos | 50 | direct | 2.373 | 2.347 | 3.369 | 3.914 | 49.67 | 447 / 450 |
| memos | 50 | proxy | 5.017 | 4.600 | 7.650 | 9.170 | 49.67 | 447 / 450 |
| memos | 50 | monitor | 5.972 | 5.452 | 9.330 | 10.445 | 49.67 | 447 / 450 |
| memos | 100 | direct | 1.590 | 1.279 | 3.189 | 3.529 | 49.67 | 447 / 900 |
| memos | 100 | proxy | 4.015 | 3.648 | 6.712 | 7.533 | 49.67 | 447 / 900 |
| memos | 100 | monitor | 4.803 | 4.390 | 7.747 | 12.888 | 49.67 | 447 / 900 |
| memos | 200 | direct | 1.101 | 0.880 | 2.641 | 3.240 | 49.67 | 447 / 1800 |
| memos | 200 | proxy | 3.017 | 2.707 | 4.391 | 8.199 | 49.67 | 447 / 1800 |
| memos | 200 | monitor | 3.412 | 2.873 | 5.725 | 13.073 | 49.67 | 447 / 1800 |

Latências acima incluem todos os status. HTTP 429 é recusa por limite, não sucesso.

| API | Carga req/s | Proxy − direta (ms) | Monitor − proxy (ms) | Total − direta (ms) | Total (%) |
| --- | ---: | ---: | ---: | ---: | ---: |
| juice | 10 | +2.875 | +0.710 | +3.585 | +17.4 |
| juice | 50 | +1.887 | +0.950 | +2.837 | +20.3 |
| juice | 100 | +131.414 | -22.470 | +108.945 | +25.7 |
| juice | 200 | +236.247 | +93.661 | +329.908 | +17.2 |
| memos | 10 | +2.121 | +1.860 | +3.981 | +183.7 |
| memos | 50 | +2.644 | +0.956 | +3.599 | +151.4 |
| memos | 100 | +2.425 | +0.789 | +3.214 | +203.0 |
| memos | 200 | +1.915 | +0.396 | +2.311 | +210.2 |

Consulte resultado.json para dispersão, overhead pareado, erros, CPU/RSS e estado.

Carga aberta em localhost, gerador/API/proxy na mesma máquina; leitura de carrinho e arquivamento idempotente de nota própria. Três configurações síncronas, sem captura bruta e com log normal do monitor habilitado. Não estima capacidade máxima nem define limiar de overhead aceitável. HTTP 429 permanece visível.
