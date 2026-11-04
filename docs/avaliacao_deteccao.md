# Etapa 4 — Avaliação da capacidade de detecção

A avaliação separa autorização esperada, tentativa de BOLA, sucesso da exploração
e decisão do monitor. A unidade é uma requisição de leitura ou alteração de
objeto; login é preparação e não entra na matriz. O gabarito é salvo antes das
chamadas HTTP e não é calculado a partir dos tokens ou alertas da CPN.

## Escopo executado

O experimento usa uma API de laboratório em localhost, um processo real de
mitmproxy reverso e o monitor incremental da etapa 3. Não utiliza instâncias de
Juice Shop ou Memos. Leitura e alteração de contas são cenários controlados
próprios, não resultados atribuídos aos estudos de caso dos autores.

A política do laboratório define Alice como proprietária da conta 6 e Bob como
proprietário das contas 9 e 12. Alice pode ler a conta 12 por compartilhamento,
mas não alterá-la. Essa política está declarada em `scripts/detection_lab.py`
independentemente da CPN.

As rotas `/accounts/{id}` são deliberadamente vulneráveis e não verificam o
proprietário. As rotas `/enforced/accounts/{id}` aplicam a política e devolvem
403 em tentativas não autorizadas. Assim, a mesma intenção de ataque pode ser
observada tanto com exploração bem-sucedida quanto com acesso negado pela API.

O modelo é derivado do exemplo de contas e inclui GET e PATCH, com as respostas
200 e, nas rotas protegidas, 403. As transições protegidas mantêm os
pré-requisitos de recursos, permitindo detectar a tentativa mesmo quando a API
responde 403. Essa escolha de modelagem é explícita; modelos que excluam erros
HTTP não têm automaticamente a mesma cobertura.

## Cenários e gabarito

| Cenário | Interações em cada repetição | Objetivo |
| --- | --- | --- |
| `lab_read` | Alice e Bob leem a própria conta e a conta alheia | Leitura legítima e BOLA bem-sucedida |
| `lab_write` | Alice e Bob alteram a própria conta e a conta alheia | Alteração legítima e BOLA bem-sucedida |
| `lab_read_denied` | Mesmas leituras nas rotas protegidas | Detectar tentativa negada pela API |
| `lab_write_denied` | Mesmas alterações nas rotas protegidas | Detectar tentativa de alteração negada |
| `lab_shared_read` | Alice lê conta 12 com permissão compartilhada | Medir efeito de permissão ausente dos Links |
| `lab_missing_context` | Alice lê conta 6 após login feito diretamente na API | Medir efeito de login não observado |

Cada requisição avaliada usa um token sintético novo e uma sessão independente.
Isso impede que outro teste forneça inadvertidamente o recurso necessário. O
login passa pelo proxy, exceto no controle de contexto ausente. Cada PATCH usa
um marcador próprio; o avaliador verifica o efeito na API e, no caso negado,
verifica que o estado não mudou. Leituras bem-sucedidas precisam devolver o ID
esperado. Não se infere sucesso de exploração apenas a partir do alerta da CPN.

## Política de alerta e métricas

Divergências de fluxo de controle ou dados são tratadas como suspeitas de BOLA.
Um evento `fired` é uma decisão negativa. Erros de captura, eventos fora do
modelo, eventos não processados e outras categorias de divergência permanecem
**sem classificação**, em vez de serem convertidos automaticamente em BOLA ou
em acesso legítimo.

| Verdade de referência | Alerta | Resultado |
| --- | --- | --- |
| Tentativa BOLA | Sim | TP |
| Tentativa BOLA | Não | FN |
| Legítima | Sim | FP |
| Legítima | Não | TN |

`cpn_detection.py` calcula:

- Precisão: `TP / (TP + FP)`.
- Recall dos classificados: `TP / (TP + FN)`.
- F1: `2*TP / (2*TP + FP + FN)`.
- Acurácia dos classificados: `(TP + TN) / classificados`.
- Cobertura: `classificados / total`.
- Detecção de ataques de ponta a ponta: `TP / total de tentativas rotuladas`, incluindo as não classificadas no denominador.

Denominadores zero produzem `null`, não 0 ou 100% arbitrários. Cobertura e
quantidade não classificada acompanham a matriz para não ocultar falhas de
captura. As métricas são agrupadas por cenário e modo (offline, incremental e
runtime), além do total e do subconjunto com contexto completo.

## Executar

Com o ambiente runtime preparado na etapa 3:

```bash
.venv-runtime/bin/python scripts/evaluate_detection.py --repetitions 10 --output redes/deteccao-experimento-1
```

O script inicia a API e o proxy, executa as chamadas sequenciais, encerra ambos e
salva os resultados. Use um diretório novo: o programa recusa sobrescrever uma
avaliação anterior. As capturas com tokens e corpos são temporárias e descartadas
no encerramento; o relatório persistido guarda rótulos, classificações e tempos,
sem credenciais HTTP.

Saídas:

- `gabarito.json`: política de referência e plano fixados antes das chamadas.
- `modelo.openapi.json`: modelo exato fornecido ao monitor.
- `eventos_rotulados.jsonl`: classificação esperada, decisão observada, resultado HTTP, evidência de sucesso e tempo por requisição.
- `resultado.json`: matrizes, métricas, cobertura, concordância entre modos, versões e hashes.
- `README.md`: tabela dos resultados por cenário.

A comparação entre modos usa os mesmos eventos capturados e a mesma política de
continuação. O offline permanece implementado por `replay_trace`, enquanto o
incremental usa `ReplaySession`. São verificadas as decisões por evento e as
marcações finais offline/incremental. A checagem da etapa 3 é a que também
confronta todas as marcações runtime intermediárias.

O código de saída é 0 quando o experimento é válido e os modos concordam, mesmo
havendo falsos positivos. Métrica imperfeita é resultado experimental, não erro
de execução. Inconsistência entre modos resulta em 1; erro de preparação/entrada
resulta em 2.

## Resultado registrado

O experimento salvo em `avaliacao_deteccao/` executou dez repetições de cada
roteiro: 180 requisições de objeto, sendo 80 tentativas BOLA e 100 legítimas.
Outros 170 logins passaram pelo proxy como preparação; dez logins foram feitos
diretamente na API no controle de contexto ausente. Preparação não infla os TN.

| Conjunto | N | TP | FP | FN | TN |
| --- | ---: | ---: | ---: | ---: | ---: |
| Leitura vulnerável | 40 | 20 | 0 | 0 | 20 |
| Alteração vulnerável | 40 | 20 | 0 | 0 | 20 |
| Leitura protegida | 40 | 20 | 0 | 0 | 20 |
| Alteração protegida | 40 | 20 | 0 | 0 | 20 |
| Leitura compartilhada | 10 | 0 | 10 | 0 | 0 |
| Contexto inicial ausente | 10 | 0 | 10 | 0 | 0 |
| **Total** | **180** | **80** | **20** | **0** | **80** |

Cobertura: 100%. Precisão global: 80%. Recall: 100%. F1: 88,89%. Acurácia:
88,89%. As mesmas classificações foram obtidas nos três modos. Das 80
tentativas, 40 tiveram exploração bem-sucedida nas rotas vulneráveis e 40 foram
negadas pela própria API; ambas as categorias geraram alertas.

Os vinte falsos positivos demonstram limitações reais do recorte de modelagem:
IDs obtidos no login não representam toda política de autorização, e ausência
de um evento produtor não prova ataque. Esses controles ficam no resultado
global e também são apresentados separadamente. Não foram ajustados os rótulos
para fazer os alertas parecerem corretos.

## Alcance das conclusões e próximos experimentos

Esse resultado sustenta preservação da detecção entre os modos nos roteiros de
laboratório. Repetir as mesmas sequências não produz diversidade equivalente a
novos ataques, nem amostras independentes. Não foram calculados intervalos de
confiança ou atribuídos percentuais da tese ao projeto.

A avaliação posterior já executou View Basket, Manipulate Basket e Memos
Archive nas aplicações reais. Consulte [o experimento completo](avaliacao_aplicacoes_reais.md)
para os 140 eventos, adaptações e limites. Os passos abaixo descrevem a
preparação usada nessa extensão:

1. Fixar versões e preparar instâncias reais do Juice Shop para View Basket e Manipulate Basket, com usuários e objetos cujo proprietário seja conhecido.
2. Produzir o gabarito externo à CPN, registrar a intenção e confirmar separadamente se a exploração teve efeito na aplicação.
3. Executar pelo proxy com as OpenAPI correspondentes, adaptar a identidade quando necessário e confrontar os três modos.
4. Acrescentar Memos com versão e cenário explicitados, sem equiparar acesso público à permissão de edição.

Tentativas a objetos inexistentes, autenticação inválida, revogação, tokens
renovados e concorrência causal ainda exigem cenários próprios. Os tempos
individuais coletados não constituem avaliação de latência/vazão ou overhead;
isso pertence à etapa 5.
