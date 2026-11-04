# Redes de Petri coloridas para análise de conformidade de APIs

Implementação independente em Python dos fluxos centrais dos Algoritmos 1 e 2 da tese de Ailton da Silva dos Santos Filho (2025), com quatro exemplos executáveis. A transformação lê os `links` de uma especificação OpenAPI; o replay compara os recursos observados com tokens associados a cada usuário.

**O replay usa SNAKES por padrão.** A emissão de tokens segue a semântica de multiconjuntos, sem deduplicação dependente da marcação. Há análise de alcançabilidade, vivacidade, deadlocks e limitabilidade para um catálogo finito de bindings, com limites e provas explicitados no relatório. Veja a [semântica e o alcance da análise](docs/semantica_snakes.md).

**A implementação e os exemplos estão executáveis. A avaliação estatística completa da tese não foi reproduzida.** Veja a [análise da tese e do código](docs/analise_tese.md) para o mapeamento, as adaptações e o trabalho experimental restante.

**Há um perfil opcional compatível com o Links2CPN.** Nos oito pares originais, ele reproduziu todos os desfechos por registro e as mesmas falhas; o modo rigoroso continua sendo o padrão. Permanecem diferenças de representação em listas unitárias do Memos e diferenças em quatro controles que exercitam peculiaridades da referência. Consulte o [relatório comparativo](docs/comparacao_links2cpn.md), a [linha de base rigorosa](docs/comparacao_links2cpn/README.md) e o [resultado do perfil compatível](docs/comparacao_links2cpn_compat/README.md).

## Executar neste ambiente

O ambiente `.venv` já está preparado:

```bash
.venv/bin/python cpn_bola.py demo
.venv/bin/python cpn_bola.py demo --analyze
.venv/bin/python -m unittest discover -s tests -v
```

Sem argumentos, `cpn_bola.py` também executa a demonstração. Ela contém divergências intencionais; encontrar essas divergências é o comportamento esperado.

Em uma instalação nova com Python 3.10 ou superior:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.lock.txt
```

Em Debian/Ubuntu, `python3-venv` precisa estar instalado para criar o ambiente. O desenvolvimento foi validado com Python 3.12.3. `requirements.lock.txt` registra as versões utilizadas; `requirements.txt` descreve as faixas de dependências.

## Redes disponíveis

| Exemplo | Fluxo modelado | Diagrama |
| --- | --- | --- |
| Contas | Login → consultar a conta recebida | [SVG](redes/contas/rede.svg) |
| Juice Shop — View Basket | Login → consultar o carrinho recebido | [SVG](redes/juice_view/rede.svg) |
| Juice Shop — Manipulate Basket | Login → adicionar item ao carrinho recebido | [SVG](redes/juice_manipulate/rede.svg) |
| Memos | Listar/criar notas → arquivar uma nota conhecida | [SVG](redes/memos/rede.svg) |

Abra também o [índice visual](redes/index.html). Os arquivos `examples/*.openapi.json` são modelos reconstruídos, e `examples/*.logs.json` contêm dados **sintéticos**, identificados por `trace_id` e `user_id`.

Cada pasta de saída contém:

- `rede.svg`, `rede.dot` e `rede.json`: estrutura da rede com marcação inicial vazia, antes de popular `p_user` para cada trace.
- `trace-NNN.svg/.dot/.json`: marcação ao término de cada trace, inclusive quando ele para por divergência.
- `resultado.json`: fitness, divergências, índices dos eventos, cobertura e marcações após disparos bem-sucedidos (`steps`). `source_index`/`sources` permitem localizar o registro original; `event_index` começa em zero.
- `analise_estados.json`, quando se usa `--analyze`: grafo explorado, conclusões e evidências sobre o domínio do trace legítimo.

SVG pode ser inserido no texto do TCC; DOT pode ser reorganizado com Graphviz. **Não são arquivos `.cpn` nativos do CPN Tools.** Os modelos executáveis são os objetos Python e suas especificações OpenAPI.

## Usar outra especificação e outros logs

```bash
.venv/bin/python cpn_bola.py model examples/contas.openapi.json --output redes/minha-rede
.venv/bin/python cpn_bola.py replay examples/contas.openapi.json examples/contas.logs.json --output redes/minha-execucao
```

Para logs históricos no formato da seção 5.3 da tese:

```bash
.venv/bin/python cpn_bola.py replay minha-api.yaml meu-log.jsonl --identity-field ip --unknown skip --output redes/experimento
```

Para reproduzir as convenções de execução do Links2CPN observado nos experimentos:

```bash
.venv/bin/python cpn_bola.py replay minha-api.yaml meu-log.jsonl --identity-field ip --links2cpn-compatible --output redes/experimento-compat
```

Esse perfil habilita a notação pontuada histórica, transforma IDs ligados em texto, expande respostas array, ignora as validações adicionais de schema e `x-cpn-equals`, descarta atividades fora da rede antes de usar seus corpos e continua após divergências. O relatório grava `profile: "links2cpn"`. Use o modo padrão para as verificações adicionais desta reimplementação.

Prefira um `user_id` ou identificador de sessão estável. `--identity-field ip` é uma escolha explícita de compatibilidade: usuários atrás do mesmo IP podem ser confundidos. `--identity-field headerAuthorization` correlaciona o cabeçalho com o token devolvido em `authentication.token` no login, armazenando um hash como identificador. Renovação de token exige correlação externa.

Por padrão, uma atividade ausente da rede encerra o trace com `ATIVIDADE_DESCONHECIDA`. `--unknown skip` permite excluir atividades fora do escopo e registra quantas foram excluídas. Um trace totalmente excluído recebe `SEM_EVENTOS_MODELADOS`, nunca aceitação silenciosa. O replay para na primeira divergência de cada trace. A ordem é a do arquivo; não há ordenação automática por timestamp. Forneça `trace_id` distintos para separar sessões do mesmo usuário.

Logs aceitos: array JSON, objeto JSON único ou JSONL, com os campos `method`, `uri`/`path`, `statusCode`/`status_code`, `requestBody`/`request_body`, `responseBody`/`response_body` e a identidade escolhida. Corpos podem ser JSON já decodificado ou strings contendo JSON. Não são aceitos corpos JSON malformados. Os exemplos mostram também o formato de traces explícitos.

`--fail-on-divergence` retorna código 1 ao encontrar traces divergentes; erros de entrada retornam 2. Por padrão, a geração bem-sucedida do relatório retorna 0, mesmo com divergências.

`--backend snakes` é o padrão do replay. `--backend python` mantém o motor anterior como referência de comparação, agora com a mesma semântica sem deduplicação. Os testes comparam os dois motores nos quatro cenários.

## Analisar outras ordens e repetições

```bash
.venv/bin/python cpn_bola.py analyze examples/contas.openapi.json examples/contas.logs.json --trace-id legitimo --target-marking redes/contas/trace-000.json --output redes/analise-contas
```

O trace escolhido fornece um **catálogo de valores e respostas**, não uma sequência obrigatória. O analisador explora ordens e repetições dos bindings desse catálogo. Somente o token do usuário é inicializado. Valores fora do catálogo não são inventados. Eventos incompatíveis com guardas, schemas ou pós-condições são listados como rejeitados; operações sem bindings aparecem em `activities_without_bindings`.

Os limites padrão são 200 estados, profundidade 20, 1.000 tokens e 10.000 arestas, ajustáveis por `--max-states`, `--max-depth`, `--max-tokens` e `--max-edges`. `null` significa inconclusivo. `complete_graph: false` informa que o grafo não foi totalmente enumerado; ainda pode existir uma prova estrutural independente para alguma propriedade. O alvo é uma **marcação exata**, não apenas um conjunto de tokens a conter.

Os catálogos legítimos dos quatro exemplos resultaram em redes **não limitadas**: repetir um produtor pode acumular tokens. A vivacidade dos bindings desses catálogos foi estabelecida pela propriedade de persistência dos arcos de retorno. Não são conclusões sobre todas as entradas possíveis das APIs.

## Convenções OpenAPI

São suportados OpenAPI 3.0.x/3.1.x, JSON/YAML, `$ref` locais não recursivos, `operationId`, `operationRef` local, parâmetros de path/query/header, corpos JSON, códigos HTTP concretos, faixas como `2XX` e `default`. Há validação de schemas e interpretação restrita de expressões de links; documentos não executam código Python.

Use JSON Pointer padrão: `$response.body#/authentication/id`. `--legacy-expressions` habilita a notação pontuada dos exemplos históricos (`/authentication.id`); essa opção não deve ser usada em documentos com pontos literais nas chaves.

Extensões explícitas usadas nos exemplos:

- `requestBody: {BasketId: "$response.body#/authentication/bid"}` no Link é interpretado por propriedade, seguindo a convenção da tese. Sem um objeto de propriedades, o Link vincula o corpo completo.
- `x-cpn-many: true` no Link aplica a expressão a cada item de uma resposta array. É como a lista de notas gera vários tokens. Sem a extensão, `/0/id` seleciona apenas o primeiro item, conforme JSON Pointer.
- `x-cpn-equals` na resposta compara campos observados com expressões da requisição, por exemplo `{"/id": "$request.path.id"}`. Explicita as pós-condições necessárias para detectar recurso corrompido; elas não são inferidas automaticamente.

## Reexecutar logs públicos

```bash
.venv/bin/python scripts/check_reference_logs.py
```

Esse comando baixa dois logs públicos de Memos a partir de um commit fixo, usa o modelo reconstruído e grava [o registro da verificação](docs/verificacao_logs_publicos.json), incluindo URLs e hashes SHA-256. Os dados brutos ficam apenas em diretório temporário. O teste legítimo foi aceito; o malicioso apresentou divergência no registro 11.

## Comparar com o programa dos autores

Com Python 3.9 disponível, prepare um ambiente separado e execute ambos os programas sobre os mesmos arquivos:

```bash
bash scripts/setup_links2cpn.sh /caminho/para/python3.9
.venv/bin/python scripts/compare_links2cpn.py --checkout .comparison/Links2CPN --reference-python .comparison/venv-reference/bin/python
.venv/bin/python scripts/compare_links2cpn.py --checkout .comparison/Links2CPN --reference-python .comparison/venv-reference/bin/python --ours-profile links2cpn --output docs/comparacao_links2cpn_compat
```

A referência fica fixada no commit `d6092c3d85adaf96a0e83a90a3bd2d287cd51325`, com as 51 dependências originais verificadas. O relatório registra versões, hashes, desfechos por evento e comparação de marcações projetadas. Veja os [critérios e limites da comparação](docs/comparacao_links2cpn.md).

## Etapa 2: replay incremental

A classe `ReplaySession` mantém a CPN em memória entre chamadas a `process(event)`.
O alimentador recebe os logs já usados na etapa 1 e pode comparar cada marcação
com o replay offline preservado:

```bash
.venv/bin/python scripts/feed_events.py examples/contas.openapi.json examples/contas.logs.json --compare-offline --output redes/incremental-contas
.venv/bin/python scripts/check_incremental.py
```

As 34 combinações locais avaliadas coincidiram nos dois modos. JSONL é lido linha
a linha; a integração com uma API ainda não faz parte desta etapa. Consulte o
[funcionamento, resultados e limites](docs/processamento_incremental.md).

## Etapa 3: proxy e análise HTTP ao vivo

O proxy reverso em modo de observação captura requisições/respostas e alimenta
as sessões CPN sem bloquear chamadas divergentes. O experimento local executa
uma API deliberadamente vulnerável, um proxy real e a comparação com offline:

```bash
.venv-runtime/bin/python scripts/check_runtime.py
```

O ambiente separado usa `requirements.runtime.lock.txt`. Veja os comandos para
[iniciar a API, o proxy e os cenários](docs/integracao_runtime.md), incluindo
identidade, expiração de sessões, resultados e limites de concorrência.

## Etapa 4: avaliação de detecção

A avaliação rotulada executa leituras e alterações legítimas e BOLA em uma API
local, incluindo tentativas negadas, compartilhamento e contexto incompleto:

```bash
.venv-runtime/bin/python scripts/evaluate_detection.py --repetitions 10 --output redes/deteccao-experimento-1
```

O experimento registrado tem 180 requisições: 80 TP, 80 TN e 20 FP, sem FN,
com cobertura de 100%. Os falsos positivos expõem limitações de modelo/contexto.
Os três modos concordaram nas decisões. Consulte a [metodologia e os limites](docs/avaliacao_deteccao.md)
e os [resultados por cenário](docs/avaliacao_deteccao/README.md). A avaliação posterior também executou Juice Shop e Memos reais: 140
requisições, 80 TP e 60 TN, sem FP/FN nos roteiros privados com contexto completo.
Veja [versões, comandos, adaptações e limites](docs/avaliacao_aplicacoes_reais.md)
e a [tabela das aplicações reais](docs/avaliacao_deteccao_real/README.md).

```bash
.venv-runtime/bin/python scripts/setup_real_apps.py
.venv-runtime/bin/python scripts/evaluate_real_detection.py --repetitions 10 --output redes/deteccao-real-experimento-1
```

## Etapa 5: desempenho e overhead

O benchmark compara API direta, proxy puro e proxy com monitor, com aplicações
recriadas a cada ensaio, aquecimento, cargas e repetições balanceadas:

```bash
.venv-runtime/bin/python scripts/benchmark_runtime.py --output redes/desempenho-experimento-1
```

O protocolo padrão tem 72 ensaios nas duas aplicações reais, taxas de 10/50/100/200
req/s e três repetições. Registra latência, vazão válida, erros, tempo interno,
CPU/RSS e um experimento complementar de crescimento de tokens. Consulte a
[metodologia e os limites](docs/avaliacao_desempenho.md) e os
[resultados registrados](docs/avaliacao_desempenho/README.md).

## Organização

| Arquivo | Responsabilidade |
| --- | --- |
| `cpn_bola.py` | Motor de multiconjuntos, disparo, divergências e replay; preserva o construtor manual inicial |
| `cpn_snakes.py` | Disparos SNAKES, expansão finita de bindings e incidência dos arcos |
| `cpn_analysis.py` | Exploração de estados, evidências e provas para subclasses verificadas |
| `cpn_openapi.py` | Algoritmo de transformação, schemas, links e normalização de rotas |
| `cpn_logs.py` | Entrada JSON/JSONL, identidade e formação de traces |
| `cpn_export.py` | Exportação SVG, DOT e JSON |
| `cpn_cli.py` | Comandos `demo`, `model`, `replay` e `analyze` |
| `tests/` | Testes de comportamento, comparação entre motores e análise de propriedades |
| `scripts/create_examples.py` | Regeneração determinística dos exemplos sintéticos |

Fitness mede a fração de traces aceitos pelo modelo. Uma divergência é um indício para análise; não comprova BOLA automaticamente. A autorização real depende das regras de negócio e da fidelidade do modelo.
