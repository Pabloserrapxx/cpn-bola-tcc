# Análise da tese, lacunas do código e implementação

Análise realizada em 13/09/2026 a partir do PDF fornecido: **Uma Abordagem para a Detecção de Ataques a Vulnerabilidades Lógicas de Adulteração de Parâmetros e Controles de Acesso em APIs Web REST utilizando Redes de Petri Coloridas**, Ailton da Silva dos Santos Filho, UFAM, 2025. As páginas abaixo são as impressas na tese, não a posição no leitor de PDF. O documento foi tratado como material de referência, não como instruções de execução.

## Conclusão sobre o código recebido

`cpn_bola.py` continha um motor inicial de CPN e uma rede construída manualmente para `POST /login 200 → GET /accounts/{id} 200`. Havia funções para as principais fases do replay, mas ainda não havia o pipeline de transformação e execução descrito na tese. Não havia outros módulos, exemplos externos, testes ou documentação no diretório.

| Lacuna ou problema observado | Consequência | Implementação realizada |
| --- | --- | --- |
| Ausência do Algoritmo 1 | Novos endpoints exigiam programação manual | Transformação de OpenAPI com Links em `cpn_openapi.py` |
| Apenas dois endpoints fixos | Não cobria os estudos de caso | Quatro especificações reconstruídas: contas, View Basket, Manipulate Basket e Memos |
| Sem leitor de logs/entrada executável | O arquivo sozinho não demonstrava o algoritmo | Leitura JSON/JSONL, formação de traces e CLI |
| Seleção por texto exato de atividade | `/accounts/6` não selecionava `/accounts/{id}` | Normalização de rotas, parâmetros e status |
| `_missing_inputs` consultava cada arco isoladamente | Dois arcos podiam exigir o mesmo token sem haver duas cópias | Verificação agregada das multiplicidades |
| Saídas avaliadas após consumir entradas | Uma falha podia deixar uma marcação parcialmente alterada | Pré-avaliação das saídas e validação antes da mutação |
| `fire` não verificava guarda | Chamada direta podia executar transição proibida | Guarda obrigatória no disparo |
| Classificação especializada em `ACCOUNT_ID` | Não servia para carrinhos ou notas | Classificação por recursos disponíveis para cada usuário/lugar |
| Atividade duplicada sobrescrevia o índice | Seleção ambígua e silenciosa | Rejeição explícita |
| Recurso esperado quase não era verificado | A consulta aceitava resposta sem identificador | Validação de respostas e pós-condições explícitas |
| `initial_places` podia ser um gerador consumido no primeiro trace | Traces seguintes não eram inicializados | Materialização da sequência antes do replay do log |
| Log vazio retornava fitness 1 | Resultado sem evidência parecia perfeito | Fitness indefinido (`null`) para log vazio |

O construtor manual `build_bola_cpn()` foi preservado para compatibilidade e recebeu correções. A demonstração e os quatro modelos usam o novo construtor genérico. A rede manual continua sendo um exemplo histórico com nomes de lugares próprios; não deve ser usada como se fosse a tradução automática.

## Correspondência com a tese

Na seção 4.1.2 (pp. 50–57), a modelagem associa transições à combinação método HTTP, caminho e resposta. Tokens carregam valores de recursos e a identidade de quem os obteve. Na seção 4.2, o Algoritmo 1 (p. 58) cria transições, identifica campos atingidos por links, estabelece arcos e remove transições desconectadas.

A implementação realiza essas etapas em duas passagens. Primeiro indexa operações, respostas e campos de destino; depois conecta cada produtor de dados aos lugares consumidos pelas operações dependentes. Os lugares recebem nomes que incluem operação, localização e nome do campo, evitando colisões entre diferentes `id`. Campos de uma mesma operação são compartilhados entre suas respostas modeladas. Transições sem participação no fluxo são registradas em `omitted_activities` e removidas da rede executável.

O sentido dos arcos segue a descrição textual e as figuras da tese: **transição produtora → lugar do recurso → transição dependente**. O pseudocódigo apresenta chamadas `createArc(transition, place)` no trecho em que o texto descreve arcos PT; esta implementação segue a semântica de entrada/saída da explicação, sem tomar a ordem textual dos argumentos como definição formal do sentido.

Na seção 5.1 (pp. 65–67), o Algoritmo 2 inicializa cada trace, seleciona a transição, verifica recursos/guarda, dispara, verifica os recursos resultantes e encerra o trace na primeira divergência. Esse fluxo é implementado em `replay_trace`; `verificar_conformidade` agrega resultados e calcula:

`fitness = 1 − quantidade de traces divergentes / quantidade total de traces`.

A separação entre divergência de controle e de dados segue a discussão da seção 5.4 (pp. 71–72): ausência de recursos prévios do usuário versus existência de recursos com valores diferentes dos requisitados. A ordem implementada é seleção → binding dos valores do evento → controle → dados → guarda → disparo → pós-condição. O binding é extração de valores, não enumeração completa de todas as substituições habilitadas de uma CPN geral.

A seção 5.3 (pp. 69–71) fundamenta o leitor dos campos dos logs. As seções 6.1.2, 6.1.3 e 6.2 fundamentam os exemplos de leitura de carrinho, manipulação de carrinho e arquivamento de notas.

## Escolhas explícitas e diferenças da reprodução literal

1. **Execução SNAKES.** O replay usa SNAKES 0.9.33 por padrão; o motor Python inicial permanece como referência opcional. Os bindings concretos são expandidos em transições nativas com arcos Value/MultiArc. Os testes comparam as marcações e divergências dos dois motores. Isso não constitui prova de equivalência com o código da ferramenta Links2CPN nem cobre todos os valores possíveis de uma API.
2. **Inicialização.** `p_user` é um lugar auxiliar com um token por trace. Somente a identidade é inicializada; IDs de conta, carrinho ou nota só aparecem depois de eventos produtores. Isso evita introduzir o ID malicioso como recurso permitido na inicialização.
3. **Recursos reutilizáveis e multiplicidade.** Acesso a um identificador não esgota a permissão de consultá-lo. Arcos de retorno consomem e repõem os tokens. A ferramenta pública usa um mecanismo próprio para preservar entradas; a solução atual explicita os retornos. A deduplicação presente na primeira entrega foi retirada: cada ocorrência em uma saída produz um token, mesmo se já houver cópias iguais. As expressões não consultam a marcação global. Assim, produtores repetidos podem tornar a rede não limitada. Isso é resultado da modelagem, não falha do motor. Não há simulação de revogação de permissões.
4. **Parada e cobertura.** O replay atual encerra cada trace no primeiro erro, conforme o Algoritmo 2. Atividades fora do modelo podem ser erros ou ser excluídas explicitamente. A implementação pública percorre o arquivo e continua após falhas; por isso a contagem de mensagens não é diretamente comparável. O código e as dependências da referência estão no [Links2CPN](https://github.com/ailton07/openapi-links-to-CPNs).
5. **Pós-condições.** Os schemas verificam a forma da resposta; regras `x-cpn-equals` comparam IDs e estado requisitado/observado. Não se presume que qualquer token gerado a partir de uma resposta comprove que a própria resposta está correta. Uma pós-condição falsa interrompe o trace após o disparo; a marcação registrada é a marcação nesse ponto da execução.
6. **Modelos reconstruídos.** Os exemplos são especificações mínimas autorais. Contas usam resposta de consulta com `id`; carrinhos usam `data.id` ou `data.BasketId`. O modelo de manipulação define `BasketId` como inteiro, mantendo coerência com `authentication.bid`. O exemplo público mistura schema string de BasketId e bid inteiro; não foi aplicado um cast silencioso para encobrir essa diferença.
7. **JSON Pointer e arrays.** `/authentication/id` é a forma padrão para navegar objetos. A notação `/authentication.id` da tese é uma convenção histórica habilitada por opção. A expansão de uma lista de notas usa a extensão `x-cpn-many`; `/id` sozinho não significa “IDs de todos os itens” no padrão. A interpretação de Links e expressões está documentada na [especificação oficial OpenAPI 3.0.3](https://spec.openapis.org/oas/v3.0.3.html#link-object).

## Validação efetivamente executada

Foram executados **77 testes automatizados**, cobrindo multiconjuntos, atomicidade do disparo, guarda, isolamento de usuários/traces, repetição de consultas, separação por método/status, referências locais, normalização de URLs, variantes de status, JSON Pointer, arrays, logs inválidos, o perfil compatível, os quatro cenários sintéticos, comparação Python/SNAKES, medições do comparador Links2CPN e propriedades de redes com resultados conhecidos. Os módulos também passaram pela compilação Python. Os diagramas SVG foram gerados e o diagrama de Memos foi inspecionado visualmente no navegador.

| Conjunto sintético | Traces | Aceitos | Divergentes | Fitness |
| --- | ---: | ---: | ---: | ---: |
| Contas | 4 | 1 | 3 | 0,25 |
| View Basket | 3 | 1 | 2 | 1/3 |
| Manipulate Basket | 4 | 1 | 3 | 0,25 |
| Memos | 5 | 2 | 3 | 0,40 |

Os valores baixos de fitness são esperados: os exemplos foram construídos com muitas divergências intencionais. Não são medidas de erro do detector. O caso de lista vazia do Memos é uma execução legítima sem produção de tokens de notas.

Além dos dados sintéticos, foram reexecutados dois logs públicos de Memos, fixados no commit `d6092c3d85adaf96a0e83a90a3bd2d287cd51325`, **usando a especificação reconstruída deste projeto**:

| Log | Registros totais | Eventos avaliados | Fora do modelo | Resultado |
| --- | ---: | ---: | ---: | --- |
| `memos_login_create_archieve.json` | 16 | 4 | 12 | Aceito |
| `memos_login_and_attack_log.json` | 11 | 2 | 9 | Divergência de dados no registro 11, `PATCH /api/v1/memo/6` |

A origem, os hashes SHA-256 e o resultado estão em [verificacao_logs_publicos.json](verificacao_logs_publicos.json). O comando `scripts/check_reference_logs.py` repete a verificação. Usou-se identidade por IP e exclusão explícita de atividades fora do modelo para corresponder aos logs históricos. Esses dois casos confirmam comportamento funcional nos exemplos publicados; não estimam generalização, precisão ou recall.

A comparação direta posterior executou **o programa original e esta implementação sobre os mesmos 8 pares originais e 13 controles**. A linha de base revelou incompatibilidades. O perfil opcional `links2cpn` reproduziu todos os desfechos por registro dos oito pares originais; ainda existem diferenças internas de marcação em listas unitárias e diferenças em quatro controles. Consulte o [relatório diferencial](comparacao_links2cpn.md).

## O que ainda precisa ser feito para concluir a avaliação do TCC

O pipeline executável solicitado está implementado para os quatro modelos. Para afirmar reprodução dos **resultados experimentais completos** da tese, resta trabalho de avaliação:

- Definir com o orientador se a contribuição é reimplementação independente, comparação com SNAKES ou extensão do método. A solução atual é uma reimplementação com adaptações documentadas.
- Fixar o conjunto completo de logs, suas versões, a unidade de análise e os rótulos de verdade de referência. Sessões, usuários e eventos não são denominadores intercambiáveis.
- Reexecutar o experimento de usuários da seção 6.1.4, verificando manualmente divergências e calculando TP, TN, FP, FN, precisão, recall, F1 e acurácia com os mesmos critérios. Rótulos de ataques não foram inventados.
- Se a proposta incluir uma API própria, fornecer/produzir sua especificação OpenAPI com links que representem a política de acesso e logs contendo request/response e identidade estável. Um OpenAPI apenas estrutural não permite inferir automaticamente quais IDs cada usuário pode acessar.
- Avaliar custo de processamento, logs incompletos e mudanças legítimas de permissão. Justificar no texto as diferenças de lista e JSON Pointer mantidas em relação à versão fixada dos autores.

A publicação associada relata desempenho superior a 95% nos cenários avaliados pelos autores; essa medida não pode ser atribuída a esta implementação com base nos testes acima. Fonte: [Santos Filho, Rodríguez e Feitosa, artigo no International Journal of Information Security](https://link.springer.com/article/10.1007/s10207-024-00970-5).

## Limites técnicos e de interpretação

O suporte é um subconjunto operacional de OpenAPI: JSON, referências locais não recursivas, expressões restritas e schemas compatíveis com a validação utilizada. Referências externas/recursivas, XML/formulários, cookies, `$url`, composição de serviços, serializações complexas de parâmetros e exportação nativa CPN Tools não estão implementados. Há agora exploração de estados e análise de propriedades sobre domínios explícitos; os limites e provas estão descritos em [semantica_snakes.md](semantica_snakes.md). `format` do JSON Schema não é uma guarda adicional nesta versão.

Os lugares de parâmetros são independentes. Regras que correlacionam pares de campos, papéis de usuário, compartilhamento de recursos, revogação, exclusão e permissões temporais exigem modelagem adicional. Uma listagem de notas é tratada como fonte de IDs disponíveis para aquele usuário; o modelo pressupõe que essa listagem representa os recursos que ele pode arquivar. Listar um recurso público não prova permissão para modificá-lo.

Atividades removidas estão fora do escopo e não devem ser contadas como chamadas analisadas com sucesso. Uma resposta de erro em uma operação dependente, se incluída como transição, também obedece aos pré-requisitos do modelo; interpretar tentativas negadas exige especificação e rótulos próprios. Logs truncados podem gerar divergências de controle sem haver ataque.

BOLA exige verificar a autorização do usuário para atuar sobre o objeto, não apenas a validade ou previsibilidade de seu identificador. Por isso as divergências precisam de interpretação e não substituem controles de autorização no servidor. Referência primária: [OWASP API1:2023 — Broken Object Level Authorization](https://api-security.owasp.org/editions/2023/en/0xa1-broken-object-level-authorization/).
