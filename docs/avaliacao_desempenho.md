# Etapa 5 — Desempenho e overhead

O experimento mede o custo da integração síncrona em duas aplicações reais:
Juice Shop 14.1.1 e o backend do fork Memos do autor. Usa as imagens, o commit
e o modo de execução registrados na [avaliação de detecção](avaliacao_aplicacoes_reais.md).
O objetivo é quantificar o impacto, sem definir previamente um percentual
arbitrário de overhead aceitável.

## Configurações comparadas

| Nome no relatório | Caminho da chamada | O que permite avaliar |
| --- | --- | --- |
| `direct` | Cliente → API | Linha de base da aplicação |
| `proxy` | Cliente → proxy → API | Encaminhamento sem carregar o addon CPN |
| `monitor` | Cliente → proxy + monitor → API | Solução completa, com verificação síncrona |

O modo monitor usa o log normal de decisões, com descarregamento a cada evento.
A captura bruta e a exportação de marcações por evento ficam desabilitadas.
Portanto o custo observado inclui o log operacional normal, mas não o custo
extra da captura de diagnóstico das etapas anteriores.

## Cargas e controle experimental

O protocolo padrão contém 72 ensaios: duas aplicações, quatro taxas (10, 50,
100 e 200 requisições/s), três configurações e três repetições. Cada ensaio tem
um segundo de aquecimento a 10 req/s e uma janela de carga de três segundos.
A preparação e o aquecimento não entram nas distribuições medidas.

A aplicação é recriada em contêiner descartável para **cada configuração de cada
repetição e carga**, com o mesmo estado inicial. O proxy e a CPN também são novos.
Os alvos são preparados antes da carga:

- Juice Shop: consulta GET do próprio carrinho de um usuário recém-criado.
- Memos: PATCH de arquivamento de uma nota privada própria de um usuário comum.
  A nota já está arquivada antes do aquecimento para tornar as repetições
  idempotentes. As respostas e alterações de timestamp continuam sendo produzidas
  pela aplicação real.

As operações produtoras passam pelo monitor na preparação, mantendo os tokens
necessários. A identidade de experimento é fornecida pelo alimentador em
`X-CPN-Session`; a autenticação real da API continua usando token/cookie.
São requisições legítimas, sem misturar ataques no teste principal de latência.
O desempenho de exploração de ataques e de manipulação de carrinho por POST não
foi medido nesta rodada.

A ordem das três configurações é sorteada com seed 42 e rotacionada a cada
repetição. Com três repetições, cada configuração aparece uma vez em cada posição,
reduzindo o viés de ordem. O host, as imagens, os modelos, a preparação e o
conjunto de chamadas são os mesmos. Caches globais do sistema não são limpos,
não há reserva exclusiva de CPU e os serviços já existentes do usuário não são
interrompidos. Isso é uma avaliação local de curta duração, não um benchmark em
máquinas dedicadas.

A validação automatizada é executada antes da rodada final, evitando executar
a suíte de testes em paralelo às janelas medidas.

## Gerador de carga

`cpn_performance.load` usa agendamento aberto: a requisição de índice `i` é
programada para `i / taxa`. Há até 16 trabalhadores com uma conexão HTTP
persistente por trabalhador. O número de chamadas oferecidas é fixo
`int(taxa * duração)` em cada configuração.

Se os trabalhadores estiverem ocupados, a fila do gerador cresce. O script
registra essa espera em vez de atrasar silenciosamente o calendário de chegada:

- `latency_ms`: da chegada programada à leitura completa da resposta, incluindo
  espera pelo gerador/cliente.
- `service_ms`: do início efetivo do trabalho HTTP à resposta completa.
- `queue_ms`: atraso entre o instante programado e o início efetivo.

Essa distinção permite identificar interferência do gerador e acúmulo de fila.
Não se interpreta uma taxa configurada como capacidade sustentada atingida. O
fim da janela é respeitado mesmo que a última chamada termine antes dela;
requisições pendentes são drenadas, e o tempo adicional entra no denominador da
vazão. Há timeout de dez segundos por conexão e não há repetição automática de
falhas.

## Métricas e overhead

Por ensaio, são registrados média, mediana, p95 e p99 das latências, além do
mínimo e máximo. A mediana usa os valores centrais; p95 e p99 usam o posto mais
próximo (`ceil(q*N)`). Há distribuições para todas as respostas e, separadamente,
para HTTP 200 com corpo validado pelo ID/estado esperado.

Duas vazões são mantidas:

- `completed_rps`: chamadas concluídas, incluindo erros, divididas pelo tempo
  total da janela e eventual drenagem.
- `successful_rps`: somente HTTP 200 com corpo válido, no mesmo denominador.

A tabela inclui contagem HTTP 200/total. HTTP 429, timeouts e corpos inválidos
não são convertidos em sucessos. Respostas rápidas de recusa podem reduzir a
latência média de todos os status; isso não significa que a aplicação ficou
mais eficiente.

O overhead é calculado por pares da mesma repetição:

```text
Diferença absoluta = métrica com intervenção − métrica de referência
Diferença percentual = diferença absoluta / referência × 100
```

Há três contrastes: proxy contra direto, monitor contra proxy e solução completa
contra direto. Para latências, valores positivos indicam atraso adicional; para
vazão, valores negativos indicam redução. Valores negativos de atraso não são
truncados: podem refletir variação experimental. Referência zero gera percentual
indefinido (`null`).

Os resultados entre repetições incluem média, desvio padrão amostral, mínimo e
máximo. Percentis são calculados por ensaio e depois agregados; não se apresenta
a média dos p99 como p99 de todas as amostras combinadas. Com três repetições,
a dispersão é descritiva, sem inferência estatística forte.

## Tempo interno, CPU e memória

A requisição medida recebe um identificador `X-CPN-Measurement-Id`, registrado
pelo monitor sem alterar o resultado da análise. Isso separa preparação,
aquecimento e medição no log. O script confere correspondência entre as respostas
HTTP e os eventos observados e que respostas válidas sejam executadas na CPN.

O relatório contém `replay_ms` para o custo de `ReplaySession.process()` e
`monitor_ms` para conversão, gestão de sessão e análise. A escrita final da linha
de log ocorre depois da medição interna; seu custo faz parte da latência HTTP,
mas não de `monitor_ms`. Os tempos não são subtraídos da latência do cliente para
inventar uma medida exata de custo de rede.

CPU e RSS são coletados de `/proc` a cada 100 ms para o processo principal da API
e para o processo Python do proxy. São registrados CPU em segundos, percentual
relativo a um núcleo (pode ultrapassar 100%), RSS inicial/final e máximo
amostrado. Não são medições de toda a memória de cgroup, cache de disco,
processos filhos ou do gerador. O pico amostrado pode deixar de capturar picos
mais curtos que 100 ms. Falhas de leitura são mantidas como dados ausentes.

O tempo interno representa custo local da verificação; não substitui a latência
fim a fim, que inclui API, comunicação, proxy e eventual espera no cliente.

## Crescimento do estado

Um experimento complementar repete o login produtor no Juice Shop, com uma sessão
fixa, e registra a marcação após 100 e 1.000 eventos adicionais. Os pontos são
ajustáveis. Há um login inicial de preparação. A coleta mantém a semântica de
multiconjuntos: tokens repetidos não são deduplicados.

Os pontos registram sessões, total de tokens, variantes SNAKES, tempo do replay
e amostra de RSS/CPU mais próxima. As cópias de marcação e corpos HTTP não são
gravadas a cada evento; o resumo do estado é solicitado somente nos pontos de
medição. Esse ensaio não avalia crescimento com IDs distintos ou número crescente
de usuários, nem prova limitabilidade. Tokens iguais podem ser representados
compactamente por contadores, portanto RSS e quantidade de tokens não precisam
crescer na mesma proporção.

## Executar e consultar resultados

O ambiente `.venv-runtime` e os artefatos reais da etapa 4 devem estar preparados.

```bash
.venv-runtime/bin/python scripts/benchmark_runtime.py --output redes/desempenho-experimento-1
```

Para uma rodada mais longa, por exemplo:

```bash
.venv-runtime/bin/python scripts/benchmark_runtime.py --duration 30 --warmup 5 --repetitions 5 --output redes/desempenho-experimento-longo
```

Uma execução específica pode usar `--apps memos --rates 10 20`. Para desabilitar
o ensaio complementar, passe `--growth-counts` sem valores. Para ampliar seus
pontos, use `--growth-counts 100 1000 10000`. O programa exige saída nova, evitando
sobrescrever experimentos.

As saídas são:

- `resultado.json`: configuração, ambiente, versões, hashes, dados de cada ensaio,
  agregações, overhead pareado e crescimento do estado.
- `README.md`: tabela de latência/vazão e overhead.
- `resumo.csv`: medidas por ensaio para tabelas e gráficos externos.
- `trials/*/samples.jsonl`: cada chamada, status e tempos.
- `trials/*/resources.json`: amostras de processos e seu resumo.
- `trials/*/result.json`: métricas do ensaio individual.
- `progress.json`: checkpoint dos ensaios já concluídos.

Capturas brutas, tokens/cookies e logs dos servidores/proxy ficam em diretórios
temporários e não são copiados para a saída. Modelos OpenAPI usados são salvos
junto aos resultados. Os contêineres do experimento são removidos ao final.

O campo `integrity_valid` informa a integridade da coleta, não que a latência
seja baixa. Um resultado com erros HTTP continua sendo relevante para desempenho.
Código de saída 0 indica conclusão com coleta íntegra; 1 indica inconsistência
na contagem/correspondência do monitor; 2 indica erro na preparação/execução.

O experimento registrado fica em [avaliacao_desempenho/README.md](avaliacao_desempenho/README.md).

## Resultados observados na rodada registrada

Foram concluídos 72 ensaios e 19.440 requisições medidas, além da preparação, aquecimento e 1.000 eventos no ensaio complementar. A coleta foi íntegra; não houve erros de transporte ou corpos HTTP 200 inválidos.

Exemplos em cargas com todas as respostas válidas, com latências médias entre três repetições:

| Aplicação / carga | Direta (ms) | Proxy puro (ms) | Proxy + monitor (ms) | Acréscimo total (ms) | Acréscimo sobre proxy (ms) |
| --- | ---: | ---: | ---: | ---: | ---: |
| juice / 10 req/s | 21.383 | 24.258 | 24.968 | +3.585 | +0.710 |
| juice / 50 req/s | 14.012 | 15.899 | 16.849 | +2.837 | +0.950 |
| memos / 10 req/s | 2.406 | 4.527 | 6.387 | +3.981 | +1.860 |

No Juice Shop a 50 req/s, o acréscimo médio pareado da solução completa foi 2,837 ms (20,3%), com desvio padrão de 0,176 ms entre repetições. O replay interno médio foi 0,405 ms. No Memos a 10 req/s, o acréscimo total foi 3,981 ms, mas o desvio padrão foi 2,354 ms: há variação relevante, que impede tratar esse valor como constante universal. O replay interno médio nesse grupo foi 0,758 ms.

Na carga de 200 req/s, o Juice Shop teve médias de latência de 1.913 ms na API direta, 2.150 ms no proxy puro e 2.243 ms com monitor. A vazão efetiva média foi, respectivamente, 96,96, 90,32 e 88,74 sucessos/s, incluindo o tempo de drenagem. Todas as respostas foram válidas, mas o sistema não sustentou a taxa oferecida. A fila do cliente e a distribuição de serviço estão nos dados individuais; não se atribui todo esse atraso ao replay.

No Memos foram registradas 5.427 respostas HTTP 429 entre as 9.720 chamadas medidas das três configurações. A partir de 50 req/s, cada ensaio teve 149 sucessos; nas janelas de três segundos isso corresponde a 49,67 sucessos/s devido ao burst inicial. A redução da latência de todos os status em cargas maiores resulta em parte das recusas rápidas. Não representa melhora de atendimento.

O pico RSS médio amostrado do processo do proxy no Juice Shop a 50 req/s foi aproximadamente 70,54 MiB sem monitor e 73,97 MiB com monitor. O uso de CPU desse processo foi de 9,66% e 13,55% de um núcleo, respectivamente. São medidas do processo principal, com as limitações de amostragem descritas acima.

No ensaio de crescimento, 100 eventos produtores adicionais resultaram em 102 tokens; 1.000 eventos resultaram em 1.002 tokens. Foi mantida uma sessão e uma variante nativa. Os tempos nos dois pontos foram 0,284 ms e 0,426 ms; são amostras pontuais, sem prova de relação causal ou tendência estatística. O RSS próximo dos pontos variou de aproximadamente 73,79 para 74,31 MiB.

Não há critério pré-fixado de aceitabilidade. Os dados demonstram custo adicional no recorte executado, com forte influência da carga, da aplicação, da intermediação e dos limites já existentes no servidor.

## Limites de interpretação

O Memos mantém seu limitador original de 30 requisições/s com burst 60. As cargas
mais altas podem medir saturação desse limitador, e não um gargalo da CPN.
Como cada ensaio começa com um bucket novo, o burst pode elevar a vazão de
sucessos da janela curta acima de 30 req/s; isso não é capacidade sustentada. Para
estudar abaixo dele, use taxas como 10 e 20 req/s; para estudar sem o limitador,
seria necessário outro experimento com alteração explicitamente registrada.

As janelas padrão são curtas. Em 10 req/s há somente 30 chamadas por ensaio;
p95/p99 têm baixa resolução e são sensíveis a variações ocasionais. Avaliar
caudas de latência para publicação exige ampliar duração e repetições. O gerador,
a API e o proxy compartilham o host; pressão no gerador também pode afetar filas
e CPU. A amostra do ambiente registra características da máquina e load average,
mas não garante isolamento de outros processos.

Esses resultados quantificam o impacto no recorte executado. Não estabelecem
capacidade máxima, desempenho em produção, comportamento com sessões distribuídas
ou um limiar universal de overhead aceitável.
