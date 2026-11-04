# Avaliação de detecção — laboratório

Unidade: requisição de objeto. Login é preparação. Tentativas negadas continuam rotuladas como ataques.

| Cenário | N | TP | FP | FN | TN | Cobertura | Precisão | Recall | F1 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| lab_missing_context | 10 | 0 | 10 | 0 | 0 | 1.000 | 0.000 | indefinido | 0.000 |
| lab_read | 40 | 20 | 0 | 0 | 20 | 1.000 | 1.000 | 1.000 | 1.000 |
| lab_read_denied | 40 | 20 | 0 | 0 | 20 | 1.000 | 1.000 | 1.000 | 1.000 |
| lab_shared_read | 10 | 0 | 10 | 0 | 0 | 1.000 | 0.000 | indefinido | 0.000 |
| lab_write | 40 | 20 | 0 | 0 | 20 | 1.000 | 1.000 | 1.000 | 1.000 |
| lab_write_denied | 40 | 20 | 0 | 0 | 20 | 1.000 | 1.000 | 1.000 | 1.000 |

Os controles de compartilhamento e contexto ausente expõem limitações do modelo. Não são resultados de Juice Shop/Memos nem evidência de desempenho sob carga.
