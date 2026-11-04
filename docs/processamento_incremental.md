# Etapa 2 — Processamento incremental

A classe `ReplaySession`, em `cpn_incremental.py`, recebe uma interação HTTP
completa por chamada a `process(event)`. A requisição e a resposta já devem estar
correlacionadas no objeto `Event`. A CPN é clonada, inicializada e associada ao
executor uma única vez por sessão. Eventos seguintes usam a marcação anterior;
o histórico não é reexecutado pelo monitor.

```python
from cpn_incremental import ReplaySession
from cpn_openapi import build_openapi_cpn

model = build_openapi_cpn('examples/contas.openapi.json')
session = ReplaySession(model, trace_id='sessao-1', user_id='alice')
# evento_login e evento_consulta são objetos Event, com user_id='alice'.
# resultado = session.process(evento_login)
# resultado = session.process(evento_consulta)
# resumo = session.finish()
```

`EventResult` informa índice local (base zero), índice de origem, atividade,
status, se houve disparo, divergência e duração em nanossegundos. Os status são
`fired`, `divergent`, `outside_model` e `not_processed`. Uma pós-condição pode
falhar após um disparo: nesse caso `status='divergent'` e `fired=True`.

O perfil rigoroso interrompe o trace na primeira divergência. Chamadas seguintes
retornam `not_processed`, sem alterar a marcação. Para continuar, configure
`continue_after_divergence=True` desde o início. `finish()` fecha a sessão,
produz um `ReplayResult` e é idempotente. Depois dele, `process()` é rejeitado.
Traces vazios ou totalmente excluídos recebem `SEM_EVENTOS_MODELADOS` somente
no encerramento. Uma exclusão temporária não encerra uma sessão que ainda pode
receber eventos modelados.

## Alimentar os logs existentes

```bash
.venv/bin/python scripts/feed_events.py examples/contas.openapi.json examples/contas.logs.json --output redes/incremental-contas --compare-offline
```

Para o perfil compatível, use `--links2cpn-compatible`. Isso aplica as mesmas
convenções de transformação e parsing da etapa 1, exclui atividades fora do
modelo e continua após divergências. Para logs históricos, selecione
explicitamente `--identity-field ip`. Também estão disponíveis `--backend`,
`--legacy-expressions`, `--unknown` e `--fail-on-divergence`.

A saída inclui:

- `eventos.jsonl`: resultado de cada evento, escrito progressivamente, sem corpos HTTP.
- `resultado.json`: resultados finais por trace, contadores de estado, comparação e tempos.

JSONL/NDJSON é lido linha a linha. Arquivos `.json` com arrays ou traces explícitos
usam o parser atual e são carregados em memória pelo alimentador. Isso não altera
a interface incremental do monitor. Os eventos mantêm a ordem do arquivo;
traces intercalados têm sessões independentes. Uma identidade diferente dentro
do mesmo trace é rejeitada. Não há espera artificial entre eventos.

O programa retorna 1 em divergência da comparação, ou em divergência de
conformidade quando `--fail-on-divergence` estiver habilitado; entradas inválidas
retornam 2. Um ataque esperado não causa erro de execução por padrão.

## Critério e resultados de equivalência

```bash
.venv/bin/python scripts/check_incremental.py
.venv/bin/python -m unittest discover -s tests -v
```

A avaliação registrada em `avaliacao_incremental.json` compara os quatro exemplos
reconstruídos e os treze controles locais nos dois perfis: 34 combinações, todas
com equivalência observada. Isso compara o incremental com o replay offline
local; não constitui uma nova execução dos oito pares originais do Links2CPN.
São registrados hashes dos arquivos de entrada e do código usado na avaliação.

O replay offline de `cpn_bola.py` foi preservado como referência independente.
Com `--compare-offline`, cada prefixo do trace é reexecutado nessa referência e
comparado com a sessão persistente. São confrontados divergências completas,
marcação com multiplicidades, contadores e ocorrência de disparo no evento.
Também são comparados os resultados finais, inclusive de sessões vazias. Os
testes verificam ainda os passos intermediários, ambos os backends, isolamento,
intercalação, encerramento e leitura incremental de JSONL.

A comparação de prefixos exige tempo quadrático em relação ao tamanho do trace
e guarda seu histórico: destina-se à validação, não ao monitor em produção.
Sem essa opção, o monitor não guarda eventos anteriores; preserva marcação,
variantes SNAKES, contadores e divergências. `record_steps=False` é o padrão.

## Desempenho e limites

O relatório mede apenas `process()`, incluindo verificações e disparo, com média
e percentis pelo método do posto mais próximo. Exclui parsing, construção do
modelo, inicialização da sessão, exportação e comparação. A coleta armazena um
tempo por evento processado; resultados `not_processed` não entram na amostra.
Esses tempos não medem latência de API ou capacidade máxima. A comparação
habilitada também perturba o ambiente; não deve ser usada para benchmark.

Não são gerados diagramas por evento. O resumo conta tokens totais, sessões e
variantes nativas finais. Não há medição de pico de memória ou vazão nesta etapa.
A sincronização de marcações e a retenção de variantes do executor SNAKES
permanecem como no offline. Tokens não são deduplicados nem removidos para
limitar artificialmente o estado.

O estado vive na memória do processo. Não há recuperação após reinicialização,
TTL, logout automático, deduplicação de entregas, proxy ou fila. Eventos
repetidos são processados novamente, conforme a semântica do replay. A sessão
exige chamadas sequenciais; vários processos não devem alterar uma mesma sessão
sem coordenação. O alimentador mantém todas as sessões até o fim da execução;
na integração online será necessário definir encerramento e descarte explícitos.
As chaves de sessão devem incorporar a versão do modelo quando houver múltiplas
versões de uma API. A captura e a ordenação de chamadas HTTP concorrentes ainda
pertencem à etapa de integração.
