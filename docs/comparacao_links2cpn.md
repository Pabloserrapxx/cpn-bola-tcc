# Comparação diferencial com Links2CPN dos autores

## Resultado principal

O modo rigoroso mostrou incompatibilidades nos arquivos originais. Depois disso foi implementado um perfil `links2cpn`, acionado por `--links2cpn-compatible`. **Nesse perfil, os oito pares originais tiveram os mesmos desfechos em todos os registros e as mesmas listas de falhas que o Links2CPN.** Isso é equivalência observada nesse conjunto, não uma prova formal para qualquer OpenAPI ou log.

Foram usados **8 pares de arquivos originais** e **13 casos controlados**. Em cada caso, os dois programas receberam os mesmos bytes de OpenAPI e log; seus hashes estão nos relatórios. No perfil compatível, houve igualdade dos desfechos por registro nos 8 originais e em 9 dos 13 controles. O primeiro erro coincidiu nesses mesmos 17 casos. Esses números descrevem os testes escolhidos; não são medidas de acurácia de detecção de BOLA.

Os dados completos estão na [linha de base do modo rigoroso](comparacao_links2cpn/README.md), no [resultado do perfil compatível](comparacao_links2cpn_compat/README.md) e em seus manifests de versões e hashes.

## O que foi executado

- Referência: [Links2CPN, commit `d6092c3d85adaf96a0e83a90a3bd2d287cd51325`](https://github.com/ailton07/openapi-links-to-CPNs/tree/d6092c3d85adaf96a0e83a90a3bd2d287cd51325).
- Ambiente da referência: Python **3.9.25**, SNAKES **0.9.32**, com **todas as 51 dependências** do `requirements.txt` nas versões fixadas. Setuptools 65.5.1 e wheel 0.38.4 foram usados como ferramentas de instalação; suas versões não são fixadas pelo arquivo dos autores.
- Ambiente do TCC: Python **3.12.3**, SNAKES **0.9.33** e as dependências registradas em `requirements.lock.txt`.
- Linha de base do TCC: `legacy_expressions=True`, identidade por `ip`, atividades fora do modelo com política `skip`, replay interrompido no primeiro erro de cada trace.
- Perfil compatível: identificadores textuais, expansão implícita de arrays, validações adicionais desativadas, leitura tolerante dos corpos e continuação depois de divergências.

O observador chama diretamente **`Replay.replay_execution_on_log` do checkout original**. Não substitui sua seleção de transição, binding, tentativa de conversão para string, disparo ou continuação após erros. A única substituição é `DrawUtils.draw`: em vez de gerar PNG, captura a marcação. Isso elimina a dependência de Graphviz durante a medição, sem alterar os tokens. O código de referência não foi editado.

A instalação inicialmente tentou usar Python 3.12, mas o SNAKES 0.9.32 depende de `imp`, removido nessa versão. O resultado final usa Python 3.9, compatível com o ambiente descrito pelos autores. Também foi necessário fixar as ferramentas de instalação para aceitar os pacotes antigos; as 51 dependências da referência foram verificadas individualmente.

## Linha de base rigorosa nos arquivos originais

| Caso | Resultado no Links2CPN | Resultado no TCC |
| --- | --- | --- |
| Contas — log legítimo | Sem falhas de disparo | Divergência na resposta do login, registro 2 |
| View Basket — legítimo | Sem falhas | Divergência na resposta do login, registro 2 |
| View Basket — múltiplos usuários | Sem falhas | Divergências nos logins, registros 3 e 4 |
| View Basket — ataque | Falha de disparo no registro 29 | Leitura interrompida por corpo não JSON no registro 16 |
| Manipulate Basket | Falha de disparo no registro 21 | Divergência na resposta do login, registro 11 |
| Experimento combinado | Falhas nos registros 47, 49, 54, 55, 96, 97, 105, 119 e 133 | Leitura interrompida por corpo não JSON no registro 61 |
| Memos — legítimo | Sem falhas | Falha ao avaliar o link da listagem, registro 9 |
| Memos — ataque | Falha de disparo no registro 11 | Falha ao avaliar o link da listagem, registro 9 |

“Falha” nessa tabela é uma ocorrência observada no programa, não um rótulo de ataque validado manualmente.

## Resultado depois do perfil compatível

Nos mesmos arquivos, sem adaptar OpenAPI ou logs, o perfil compatível coincidiu com o original em todos os desfechos por registro:

- Contas e os dois logs legítimos de View Basket terminaram sem falhas em ambos.
- View Basket ataque falhou no registro 29 em ambos.
- Manipulate Basket falhou no registro 21 em ambos.
- O experimento combinado falhou nos registros 47, 49, 54, 55, 96, 97, 105, 119 e 133 em ambos.
- Memos legítimo terminou sem falhas em ambos; Memos ataque falhou no registro 11 em ambos.

Foram comparadas 38 marcações após eventos disparados pelos dois programas nos arquivos originais. Quatro diferiram, todas no Memos legítimo, porque a referência guarda a lista unitária como um `MultiSet` aninhado e esta implementação guarda um token simples. Os valores e multiplicidades lógicos são os mesmos nesses pontos, mas a estrutura interna não é idêntica.

### Causas localizadas na linha de base

**Schemas e conversão de tipos.** Nos modelos originais de contas e carrinhos, `authentication.umail` está declarado como inteiro, mas os logs contêm uma string. Nossa implementação valida a resposta inteira e interrompe antes de verificar o acesso posterior. O Links2CPN usa os campos envolvidos nos links e transforma valores de recursos em strings. Existe ainda diferença entre alguns IDs numéricos de origem e os schemas string dos destinos. Portanto, a validação adicional altera a compatibilidade; não é correto chamar essas rejeições de novos ataques detectados.

**Listas de Memos.** Os modelos originais usam `$response.body#/id` em uma resposta array. Os autores interpretam isso como extração de IDs da lista. Nosso modelo exige `x-cpn-many` para essa expansão. A opção `legacy_expressions` atual cobre a notação pontuada, mas não ativa automaticamente a convenção de arrays. Por isso o processamento para no registro 9.

**Corpos fora do escopo.** Os registros 16 e 61 que interrompem nosso leitor são respostas `POST /rest/user/login 401`, ausentes da rede gerada nesses casos. O Links2CPN exclui a atividade antes de precisar interpretar o corpo. Nosso leitor decodifica os corpos antes da seleção de atividades e falha mesmo com `--unknown skip`. Esse é um ponto concreto de incompatibilidade do pipeline.

**Topologia.** Nos oito pares originais, o número de transições geradas coincide entre as implementações. Nosso modelo acrescenta um lugar `p_user`; por exemplo, contas tem 1 lugar/2 transições na referência e 2 lugares/2 transições no TCC. Memos tem 1 lugar/7 transições na referência e 2 lugares/7 transições no TCC. Igualdade dessas contagens não demonstra igualdade das redes ou de seus comportamentos.

## Casos controlados na linha de base

Os controles estão em `examples/comparison/`, gerados deterministicamente por `scripts/comparison_fixtures.py`. Cada especificação/log é fornecido sem mudanças a ambos os programas. São dados sintéticos, e seus IDs usam strings coerentes entre origem e destino nos casos básicos, para isolar a semântica do fluxo.

| Controle | Observação |
| --- | --- |
| Consulta legítima repetida | Ambos disparam os mesmos eventos; marcações projetadas coincidem |
| Operação sem produtor prévio | Ambos falham no primeiro evento |
| ID alheio | Ambos falham no acesso ao ID não produzido |
| Continuação após erro | Primeiro erro igual; o original continua e o TCC encerra o trace |
| Dois usuários | Ambos preservam o isolamento por identidade nos eventos testados |
| Dois logins iguais | Ambos mantêm duas cópias do recurso; a consulta preserva a multiplicidade |
| Pós-condição de ID da resposta | O TCC acusa divergência; o original não aplica `x-cpn-equals` |
| JSON Pointer com `/authentication/id` | O TCC executa; o original falha, pois espera sua notação pontuada |
| Quantidade zero com guarda de schema | O TCC rejeita; o original não aplica essa guarda |
| Lista com dois itens | Ambos expandem os recursos e permitem o acesso testado |
| Lista com um item | O original deixa um `MultiSet` aninhado e falha no acesso; o TCC produz um token simples |
| Lista vazia | O original registra erro na extração; o TCC aceita uma emissão vazia |
| Lista com dois links de destino | O original preenche somente o primeiro destino; o TCC preenche os dois |

Os dois últimos comportamentos de representação estão associados ao [plugin `infinite_transition.py` da referência](https://github.com/ailton07/openapi-links-to-CPNs/blob/d6092c3d85adaf96a0e83a90a3bd2d287cd51325/src/openapiLinks2CPNs/utils/infinite_transition.py): ele trata especialmente listas com mais de um item e retorna de `fire` após esse tratamento. Os controles reproduziram o efeito no destino seguinte. Não foram modificados esses comportamentos para facilitar a comparação.

Com o perfil compatível, os desfechos passaram a coincidir em 9 dos 13 controles. Continuam diferentes apenas o JSON Pointer padrão, lista unitária, lista vazia e dois destinos. Os três últimos preservam uma semântica regular de lista em vez das peculiaridades observadas no plugin original.

## Como as marcações foram comparadas

O original usa valores string e nomes de lugares diferentes. A comparação de marcação é, portanto, uma **projeção declarada de recursos**, não igualdade literal de toda a CPN:

1. Exclui o lugar auxiliar `p_user` do TCC.
2. Alinha lugares por operação, caminho e campo de destino.
3. Compara recursos da mesma identidade, com o identificador do usuário representado por hash no observador.
4. Representa IDs escalares por strings para comparar valores lógicos entre os motores.
5. Preserva quantidade de cópias e profundidade de aninhamento de `MultiSet`.

No perfil compatível foram comparadas **22 marcações de eventos disparados por ambos nos controles**. Houve diferenças em duas: lista com um item e lista com dois destinos. Não são consideradas equivalentes as marcações de eventos que um programa deixou de executar, nem são comparadas como igualdade as representações JSON tipadas e string originais. Corpos de log e credenciais não foram copiados para os relatórios.

O relatório distingue:

- `first_failure_agreement`: igualdade do primeiro erro; pode ser verdadeira mesmo que o restante da execução difira.
- `same_event_outcomes`: igualdade dos desfechos de todos os registros do arquivo, incluindo diferenças de continuação.
- `comparable_marking_count`: quantidade efetivamente comparada, evitando interpretar “zero diferenças” como evidência quando não houve comparação.

## Implicações para o TCC

A reexecução anterior de dois logs públicos de Memos usava **nosso modelo reconstruído**. Ela continua sendo uma verificação válida daquele modelo, mas não era uma comparação com a mesma especificação dos autores. Esta comparação mais forte expôs a diferença da expansão de arrays no OpenAPI original.

O perfil compatível aplica estas decisões:

1. Separar um perfil compatível com a referência das validações adicionais por schema/pós-condição. Conservar a distinção entre erro de qualidade de dados e suspeita de ataque.
2. Tratar atividades fora do escopo antes de exigir JSON válido em seus corpos, registrando essa exclusão.
3. Definir uma política explícita de conversão de IDs e de expansão das listas históricas.
4. Distinguir parada no primeiro erro por trace, prevista no pseudocódigo da tese, da continuação por arquivo usada no software publicado.
5. Decidir se diferenças reproduzidas da referência, como o aninhamento de lista unitária e a interrupção de múltiplas saídas, devem ser preservadas em modo de compatibilidade ou registradas como correções da reimplementação.

Os quatro primeiros ajustes foram implementados no perfil opcional `links2cpn`. Não foram copiados três comportamentos problemáticos: rejeitar JSON Pointer padrão, aninhar `MultiSet` quando uma lista contém um item e abandonar os demais arcos de saída depois de expandir o primeiro. Por isso o perfil é uma reprodução compatível dos experimentos originais, mas não uma cópia bug a bug de toda a implementação.

A avaliação estatística completa com rótulos e critérios da tese permanece uma etapa distinta.

## Reproduzir

É necessário disponibilizar Python 3.9, além do `.venv` principal já preparado. O setup mantém a referência separada:

```bash
bash scripts/setup_links2cpn.sh /caminho/para/python3.9
.venv/bin/python scripts/compare_links2cpn.py --checkout .comparison/Links2CPN --reference-python .comparison/venv-reference/bin/python
.venv/bin/python scripts/compare_links2cpn.py --checkout .comparison/Links2CPN --reference-python .comparison/venv-reference/bin/python --ours-profile links2cpn --output docs/comparacao_links2cpn_compat
```

O setup baixa o checkout fixado e instala os requisitos originais. O comparador recusa alterações em arquivos rastreados da referência, Python diferente de 3.9 e divergências das versões fixadas. A única dependência externa da preparação é o acesso aos repositórios/pacotes públicos; a execução dos casos usa arquivos locais.

Na execução realizada neste ambiente, os caminhos usados foram `/tmp/cpn-links2cpn-comparison` e `/tmp/cpn-reference-venv/bin/python`. Esses diretórios temporários não são necessários para reproduzir o experimento pelo setup acima.

```bash
.venv/bin/python -m unittest discover -s tests -v
```

A suíte atual passou em **77 testes**, incluindo o perfil compatível, multiplicidades, aninhamento, exclusão do lugar auxiliar, continuação após erro, corpos tolerados e determinismo dos controles. Os 21 casos de integração com a referência são executados separadamente pelo comando de comparação.
