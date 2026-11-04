# Semântica SNAKES e análise de propriedades

## Correção do recorte inicial

A primeira entrega preservava o motor próprio, deduplicava emissões de tokens e executava somente replay de logs. Isso atendia aos exemplos funcionais, mas não justificava apresentar equivalência formal com a rede da tese. A revisão incorpora SNAKES, remove a deduplicação e acrescenta análise de estados.

Há uma distinção metodológica: **o Algoritmo 2 da tese é um replay de eventos observados**. Explorar outras sequências é uma análise complementar. Acrescentá-la melhora a avaliação da rede, mas não significa que o replay deva deixar de seguir o log.

## Motor e representação

A transformação OpenAPI continua criando um template com expressões de entrada/saída. Para cada binding concreto, `SnakesExecutor` cria uma transição em um `snakes.nets.PetriNet`, usando lugares tipados, arcos `Value`/`MultiArc` e uma guarda constante, pois a guarda do binding já foi avaliada. A biblioteca verifica a habilitação e executa `Transition.fire(Substitution())`. A marcação da camada Python é então sincronizada com o resultado nativo.

Essa construção é uma **expansão finita de bindings**. Não é um compilador simbólico que enumera todos os objetos JSON admissíveis pelo OpenAPI. Bindings com os mesmos arcos e a mesma atividade podem compartilhar uma transição nativa, mas cada ocorrência do evento continua produzindo seus tokens normalmente. Para uso das primitivas, consulte a [documentação oficial SNAKES](https://snakes.ibisc.univ-evry.fr/tutorial/first-steps-with-snakes.html).

O backend `snakes` é o padrão; `python` permanece para comparação diferencial. Os testes confrontam aceitação, divergências, marcações intermediárias e finais dos dois motores. Essa comparação valida o comportamento nos testes; não demonstra equivalência universal com o repositório Links2CPN.

## Equação de disparo e retirada da deduplicação

Para um binding `b` habilitado:

`M'(p) = M(p) − Pre(p,b) + Post(p,b)`.

Todos os termos são multiconjuntos. A habilitação exige `Pre(p,b) ≤ M(p)` para cada lugar e uma guarda satisfeita. Duas ocorrências iguais de um token exigem/produzem duas unidades, não uma.

Nos modelos gerados, `Post = Pre + recursos emitidos pelos links`: os arcos de retorno preservam os recursos lidos. As saídas dependem dos valores do binding/evento e não consultam a marcação. Foi removida a regra “produzir somente se o token ainda não existir”.

Exemplo: partindo de um token USER e zero tokens de conta, um login produz uma conta; repetir o login produz outra cópia. Consultar a conta consome uma cópia e devolve uma cópia. Logo, consultas preservam a quantidade existente, enquanto novos logins podem aumentá-la sem limite. Não se introduz um limite artificial para fazer a rede parecer limitada.

## Domínio de análise

O comando `analyze` seleciona um trace como catálogo. Cada evento válido fornece uma alternativa de binding; a ordem do catálogo não restringe a exploração e os bindings podem ser repetidos. Apenas o token USER é inicializado. Recursos de respostas futuras não entram na marcação inicial.

Eventos fora do modelo, sem dados necessários ou incompatíveis com guardas, schemas e pós-condições ficam registrados como rejeitados. Operações sem bindings no catálogo aparecem em `activities_without_bindings`. Um catálogo sem bindings válidos é rejeitado pela CLI.

Essas escolhas delimitam as conclusões: elas valem para os usuários, IDs, respostas e operações representados pelo catálogo. Não são uma prova de segurança sobre entradas desconhecidas, sobre todos os usuários ou sobre a implementação real do servidor.

## Exploração e significados dos resultados

O analisador percorre em largura as marcações, executando os modos habilitados no SNAKES. Preserva multiplicidades na identificação dos estados e restaura a marcação inicial ao terminar.

| Campo | Interpretação |
| --- | --- |
| `complete_graph` | Todas as marcações alcançáveis da rede fornecida foram enumeradas |
| `cutoff_reasons` | Limite de estados, profundidade, tokens ou arestas; ou parada solicitada após testemunha de crescimento |
| `target.reachable` | O alvo exato foi alcançado (`true`), sua ausência foi provada por grafo completo (`false`) ou falta evidência (`null`) |
| `bounded` | Existe limite para a quantidade de tokens em cada lugar, não para os valores possíveis das cores |
| `place_bounds` | Limites provados; `null` precisa ser interpretado junto da prova e dos lugares não limitados |
| `observed_max_tokens` | Máximos somente na parte explorada; não são automaticamente limites globais |
| `live` | Vivacidade de todos os bindings nativos do domínio |
| `all_activities_live` | Vivacidade das atividades, agregando seus bindings alternativos |
| `deadlock_free` | Ausência de marcações alcançáveis sem qualquer disparo habilitado |
| `structural_proof` | Evidência adicional para uma subclasse cuja estrutura foi verificada |

Vivacidade significa que, de cada marcação alcançável, ainda existe uma sequência que permite habilitar o binding/atividade. Não significa estar habilitado em todos os estados. Também não significa que o binding seja executado por todo escalonamento possível.

Num grafo completo, a vivacidade é verificada por busca reversa a partir dos estados de habilitação. Um deadlock alcançável fornece contraexemplo de vivacidade para transições existentes. Uma rede sem transições tem vivacidade universal apenas no sentido lógico vazio; por isso a CLI não aceita esse caso como análise de um catálogo de API.

## Provas além da enumeração completa

### Caminho que aumenta a marcação

Para redes com arcos constantes e guardas literais, um caminho de uma marcação `M` para outra `M'` com `M' ≥ M` e crescimento estrito em pelo menos uma cor/lugar é uma testemunha de não limitação. A mesma sequência pode ser repetida, pois há todos os tokens necessários e os arcos têm efeito fixo. O relatório registra o prefixo alcançável, a sequência repetível e as duas marcações. Essa regra não é aplicada a arcos de inibição, remoção global ou expressões arbitrárias.

### Persistência dos modelos reconstruídos

O código verifica se todas as entradas constantes são devolvidas nas saídas e se os tipos/guardas permitem os bindings. Nessa subclasse, marcações nunca diminuem e uma habilitação obtida persiste.

A análise satura as demandas finitas de tokens de cada cor/lugar: executa produtores habilitáveis em quantidade suficiente para atingir os maiores pré-requisitos do catálogo. A sequência de ativação fica registrada. Se um binding nunca habilita nessa saturação, ele não pode habilitar nesse domínio; se habilita, permanece possível após qualquer prefixo alcançável. Isso estabelece a vivacidade por binding e por atividade nessa subclasse.

Se algum binding habilitável tiver produção líquida positiva, sua repetição prova crescimento ilimitado nos lugares afetados. Lugares sem crescimento possível mantêm a quantidade inicial. São argumentos específicos para arcos persistentes, não um algoritmo universal de verificação de CPNs arbitrárias.

## Resultados reproduzidos nesta revisão

Com `demo --analyze`, usa-se o catálogo `legitimo` de cada cenário:

| Cenário | Estados enumerados | Grafo completo | Limitada | Bindings vivos | Evidência |
| --- | ---: | --- | --- | --- | --- |
| Contas | 21 | Não, profundidade 20 | Não | Sim | Arcos constantes persistentes e emissão repetível |
| View Basket | 21 | Não, profundidade 20 | Não | Sim | Mesma propriedade estrutural |
| Manipulate Basket | 21 | Não, profundidade 20 | Não | Sim | Mesma propriedade estrutural |
| Memos | 200 | Não, limite de estados | Não | Sim | Listar/criar podem repetir emissões; recursos são devolvidos |

O fato de o grafo estar incompleto não invalida uma prova estrutural independente. Sem essa prova, as propriedades que dependem de explorar o restante ficam como `null`.

No catálogo `bola-id-alheio` de contas, só o ID 6 é produzido e a consulta ao ID 9 permanece sem habilitação; a vivacidade dessa consulta é falsa nesse domínio. Isso é diferente de afirmar que o servidor impede o acesso: o evento divergente do log pode justamente representar que o servidor permitiu uma operação fora do modelo.

## Executar

```bash
.venv/bin/python cpn_bola.py demo --analyze
.venv/bin/python cpn_bola.py analyze examples/contas.openapi.json examples/contas.logs.json --trace-id legitimo --target-marking redes/contas/trace-000.json --output redes/analise-contas
.venv/bin/python cpn_bola.py analyze examples/contas.openapi.json examples/contas.logs.json --trace-id bola-id-alheio --output redes/analise-contas-bola
.venv/bin/python -m unittest discover -s tests -v
```

Foram executados 77 testes, incluindo verificações do perfil compatível e das medições do comparador Links2CPN. Além dos exemplos da API, eles incluem ciclo limitado e vivo, deadlock terminal, transição morta sem deadlock global, crescimento ilimitado, multiplicidades, tipos inválidos, alvo alcançável/inalcançável e cortes de exploração. Foi tratada uma particularidade de SNAKES 0.9.33: em transições-fonte sem entradas, `modes()` pode retornar vazio mesmo quando a substituição vazia está habilitada; o analisador consulta essa habilitação explicitamente.

A [comparação com o Links2CPN fixado](comparacao_links2cpn.md) foi executada em 8 pares originais e 13 controles. O perfil compatível reproduziu todos os desfechos por registro dos pares originais, com diferenças internas nas marcações de listas unitárias. Continua pendente a reprodução da avaliação estatística completa da tese. A integração da biblioteca e a análise de propriedades não demonstram equivalência universal com a referência.
