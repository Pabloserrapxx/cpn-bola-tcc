# Etapa 3 — Integração HTTP em tempo de execução

O projeto agora tem um proxy reverso com mitmproxy e um addon que converte
interações HTTP reais em eventos para `ReplaySession`. A integração opera em
modo de observação: a API recebe a requisição e produz a resposta; o monitor
analisa a interação completa e registra o resultado. O addon não modifica
requisições, respostas ou cabeçalhos e não bloqueia divergências.

```text
Cliente → mitmproxy reverso → API
             ↑                │
             └──── resposta ──┘
             │
             └→ RuntimeMonitor → ReplaySession → CPN da sessão
```

A captura ocorre no hook `response`, com corpos completos e descomprimidos.
A implementação usa o encaminhamento HTTP do mitmproxy, em vez de implementar
um servidor proxy próprio. Referências: [modo reverso](https://docs.mitmproxy.org/stable/concepts/modes/#reverse-proxy),
[hooks HTTP](https://docs.mitmproxy.org/stable/api/events.html) e
[addons](https://docs.mitmproxy.org/stable/addons/overview/).

## Ambiente

O ambiente `.venv-runtime` já está preparado neste workspace com Python 3.12 e
mitmproxy 12.3.0. As dependências das etapas anteriores não foram alteradas.
Para recriar, em um ambiente com suporte a venv/pip:

```bash
python3.12 -m venv .venv-runtime
.venv-runtime/bin/python -m pip install -r requirements.runtime.lock.txt
```

Neste sistema, `ensurepip` não está disponível. A alternativa usada foi criar
`.venv-runtime` sem pip e instalar com o pip do ambiente principal:

```bash
python3 -m venv --without-pip .venv-runtime
.venv/bin/python -m pip --python .venv-runtime install -r requirements.runtime.lock.txt
```

`requirements.runtime.txt` declara dependências diretas; o lock registra todas
as versões instaladas na execução validada.

## Executar a demonstração

Terminal 1, API local de laboratório:

```bash
.venv/bin/python scripts/demo_api.py --port 3000
```

Essa API é deliberadamente vulnerável: aceita os tokens sintéticos de Alice e
Bob e devolve qualquer uma das duas contas, sem conferir seu proprietário.
Alice recebe a conta 6 e Bob recebe a conta 9. Não é um experimento com Juice Shop
ou Memos. O servidor da demonstração escuta somente em localhost.

Terminal 2, proxy e monitor:

```bash
.venv-runtime/bin/python scripts/run_proxy.py examples/contas.openapi.json --upstream http://127.0.0.1:3000 --port 8080 --output redes/runtime-demo
```

Use um diretório de saída novo para cada execução. O proxy não sobrescreve os
logs de uma execução anterior. Para finalizar e produzir o resumo, use Ctrl+C.

Terminal 3, chamadas reais:

```bash
curl -s http://127.0.0.1:8080/login -H 'Content-Type: application/json' -d '{"user":"alice"}'
curl -s http://127.0.0.1:8080/accounts/6 -H 'Authorization: Bearer lab-alice'
curl -s http://127.0.0.1:8080/accounts/9 -H 'Authorization: Bearer lab-alice'
```

Resultados esperados no monitor: `fired`, `fired`, `divergent`. A última chamada
continua recebendo HTTP 200 e a conta 9, porque o experimento observa a
vulnerabilidade sem bloqueá-la. Uma chamada legítima seguinte continua sendo
analisada: o runtime usa `continue_after_divergence=True`. Para encerrar a análise
da sessão no primeiro erro, use `--stop-on-divergence`.

## Sessões e identidade

Por padrão, o coletor correlaciona o token devolvido em
`authentication.token` pelo login com o `Authorization: Bearer ...` das chamadas
seguintes. O identificador armazenado é o mesmo hash usado pelo parser da etapa
1. Outros formatos de login exigem um adaptador de identidade. Não há validação
criptográfica de JWT ou consulta à identidade interna da aplicação.

`--identity-header X-CPN-Session` permite usar um cabeçalho explícito de sessão
em laboratório ou fornecido por um componente confiável. Ele não deve ser
interpretado como identidade autenticada quando puder ser escolhido livremente
pelo cliente. O proxy não infere identidade por IP. Chamadas sem identidade
produzem `capture_error`, sem serem contadas como conformes ou BOLA.

O estado é mantido por identidade dentro de uma instância com um modelo fixo.
Troca de token cria outra sessão. O TTL padrão é 1.800 segundos de inatividade;
a limpeza é feita na chegada de um evento com identidade válida. Uma nova sessão
após expiração ganha outro identificador de trace. Esse descarte perde o
histórico de recursos e pode produzir divergências se o usuário não repetir os
produtores necessários. Não há revogação de permissões ou correlação de tokens
renovados. Expirações ficam no log; o resumo final contém as sessões ativas.

O limite padrão é de 1.000 sessões. Ao atingir a capacidade, novas identidades
ficam sem análise e geram `capture_error` com motivo `session_capacity`;
a resposta HTTP continua sendo encaminhada. Limites são ajustáveis por
`--session-ttl`, `--max-sessions` e `--max-body-bytes`.

## Saídas e tempos

`monitor.jsonl` é escrito e descarregado a cada decisão. Registra ID do evento,
ID do fluxo, trace, operação, classificação, tipo de divergência e tempos.
Corpos e cabeçalhos não são gravados nesse arquivo. Falhas de captura são
separadas de divergências da CPN; falhas de conexão ao upstream recebem
`transport_error`. Esses casos devem ser considerados na cobertura experimental.

- `result.elapsed_ns`: custo do processamento incremental.
- `monitor_ns`: captura/conversão, gestão da sessão, processamento e eventual gravação da captura; exclui a escrita da linha final de monitoramento.
- `interaction_to_decision_ns`: tempo desde os cabeçalhos da requisição até a decisão; inclui espera pela API, não é apenas custo da verificação.
- `received_at` e `decision_at`: instantes UTC para correlação; durações usam relógio monotônico.

`resultado.json` é gerado no encerramento normal. SIGKILL ou queda do processo
podem impedir esse resumo, embora linhas já descarregadas continuem no arquivo.
Não há restauração de marcações após reinício.

A opção `--capture-events` gera `eventos_capturados.jsonl` com eventos completos
e cópias das marcações runtime, para comparação independente com offline.
Essa captura é desabilitada por padrão: inclui corpos e cabeçalhos, podendo
conter credenciais e dados privados. Também adiciona custo de cópia e escrita.
No experimento automático, usa somente credenciais sintéticas e um diretório
temporário descartado no fim.

## Experimento executável e resultado

```bash
.venv-runtime/bin/python scripts/check_runtime.py
.venv/bin/python -m unittest discover -s tests -v
```

O experimento abre a API local e um processo real de proxy, executa as chamadas,
encerra ambos e registra `docs/avaliacao_runtime.json` com versões e hashes.

| Interação | Resultado esperado e observado |
| --- | --- |
| Login de Alice, conta 6 | `fired` |
| Alice consulta conta 6 | `fired` |
| Login de Bob, conta 9 | `fired` |
| Bob consulta conta 9 | `fired` |
| Alice consulta conta 9 | `divergent` — fluxo de dados |
| Alice consulta conta 6 após o alerta | `fired` |
| Health check autenticado, fora da rede | `outside_model` |

Foram preservados status HTTP, bytes do corpo e um cabeçalho de resposta
controlado em todas as chamadas, comparando acesso direto e via proxy.
A avaliação confronta decisões e marcações runtime após cada evento com replay
offline dos prefixos capturados, além dos resultados finais das duas sessões.
Isso demonstra captura e análise online nesse cenário controlado; não demonstra
integração com todas as APIs nem desempenho sob carga.

## Concorrência e avaliação de desempenho

Os hooks de análise são síncronos e as chamadas ao monitor são protegidas por
lock. A análise ocorre antes de o proxy liberar a resposta ao cliente: não há
fila assíncrona nesta implementação, e o custo pode aumentar a latência.

O monitor usa a ordem de conclusão das respostas. Chamadas concorrentes de uma
mesma sessão podem terminar em ordem diferente da chegada das requisições.
O proxy não serializa chamadas à API nem reordena eventos depois da captura.
O experimento principal usa chamadas sequenciais; políticas para concorrência
causal precisam ser definidas antes de estender as conclusões a esse cenário.

Para a futura avaliação de overhead, compare API direta, proxy puro e proxy com
monitor. O modo `--no-monitor` inicia o mesmo encaminhamento sem carregar o addon:

```bash
.venv-runtime/bin/python scripts/run_proxy.py examples/contas.openapi.json --upstream http://127.0.0.1:3000 --port 8080 --no-monitor
```

O coletor espera corpos completos e JSON nas operações modeladas. SSE, streaming
e WebSocket não são analisados. Corpos maiores que 1 MiB são recusados para
análise, sem bloquear a API, mas esse limite é aplicado depois do buffering pelo
proxy: não é um limite de memória de rede. Captura HTTPS, compressão, uploads e
protocolos além do HTTP JSON local não foram validados pelo experimento atual.
TLS pode ser tratado pelo mitmproxy, mas não foi configurado neste laboratório.
O SNAKES ainda acumula tokens e variantes conforme a semântica existente; TTL e
limite de sessões não constituem limite de memória por sessão.
