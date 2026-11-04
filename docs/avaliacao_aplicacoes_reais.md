# Etapa 4 — Detecção no Juice Shop e no Memos reais

A avaliação foi estendida às aplicações reais, em contêineres locais descartáveis,
com requisições passando pelo proxy reverso. O gabarito usa os proprietários dos
objetos verificados na preparação, sem consultar os alertas ou tokens da CPN.
Os resultados do laboratório anterior permanecem disponíveis separadamente.

## Aplicações e versões

| Aplicação | Referência executada | Adaptação |
| --- | --- | --- |
| Juice Shop | Imagem oficial `bkimminich/juice-shop:v14.1.1`, fixada pelo digest | A versão coincide com a declarada no fork instrumentado do autor; a captura usa o proxy, sem logger Winston |
| Memos | Backend do fork `ailton07/memos-with-BOLA`, commit `ee5f617ea815ea079a41dde2f53193250a22c36a` | Compilação do Go sem alterar arquivos rastreados; modo `dev`, versão reportada 0.16.0, schema de desenvolvimento |

O Juice Shop executado é o software real, mas **não é o binário compilado do fork
instrumentado**. Essa diferença está registrada, pois não cabe afirmar identidade
integral dos artefatos. O Memos executa o backend do fork do autor, com a
verificação de propriedade revertida em `UpdateMemo`. Não foi necessário compilar
o frontend para os testes REST; o placeholder já rastreado pelo fork foi mantido.

Fontes: [fork Juice Shop](https://github.com/ailton07/juice-shop-with-winston/tree/079ea6d65463b99c0d25a9ad116127575ce96e9a),
[fork Memos](https://github.com/ailton07/memos-with-BOLA/tree/ee5f617ea815ea079a41dde2f53193250a22c36a).
Os digests das imagens, o hash do executável Memos, versões, modelos OpenAPI e
hashes dos scripts estão no relatório persistido.

## Preparação reproduzível

Pré-requisitos: Docker acessível e o ambiente `.venv-runtime` da etapa 3.

```bash
.venv-runtime/bin/python scripts/setup_real_apps.py
.venv-runtime/bin/python scripts/evaluate_real_detection.py --repetitions 10 --output redes/deteccao-real-experimento-1
```

Use um diretório de saída novo. O setup baixa as imagens por digest, obtém o
checkout Memos fixado e compila o backend em um contêiner Go. O código-fonte e o
binário ficam em `.runtime-apps/`, ignorado pelo Git. Checkouts com outro commit
ou alterações rastreadas são recusados. Não é feita instalação de Go ou Node no
sistema anfitrião.

O avaliador inicia seus próprios contêineres, com portas aleatórias publicadas
apenas em `127.0.0.1`. Ele não usa nem altera aplicações existentes do usuário.
O banco Memos fica em tmpfs; o Juice Shop usa a inicialização própria da imagem.
Os contêineres criados pelo script são encerrados ao fim, inclusive em erros.
Capturas HTTP com credenciais de teste ficam em diretórios temporários e são
descartadas; as saídas persistidas não contêm tokens ou cookies.

## Gabarito e cenários

No Juice Shop, são criados dois usuários. Os IDs dos usuários vêm da resposta
de registro; os IDs dos carrinhos vêm do login, e a relação é confirmada por
`UserId` na consulta direta de cada carrinho. No Memos, um host de preparação
cria dois usuários comuns (`USER`); notas privadas são criadas e seu `creatorId`
é conferido. O host não participa dos acessos avaliados.

Antes de cada requisição alvo, o caso é escrito e descarregado em
`gabarito.jsonl`, com ator, proprietário, objeto e rótulo. Para os objetos
privados selecionados, `actor_id != owner_id` define uma tentativa BOLA. A
classificação do detector não participa dessa regra.

| Cenário | Acesso legítimo | Tentativa BOLA | Confirmação do efeito |
| --- | --- | --- | --- |
| View Basket | GET do próprio carrinho | GET do carrinho do outro usuário | ID do carrinho retornado |
| Manipulate Basket | POST de item ao próprio carrinho | POST com duas chaves `BasketId`: próprio primeiro, alheio por último | Resposta e consulta pelo proprietário confirmam produto, carrinho e quantidade |
| Manipulate negado | Não há negativos neste subgrupo | POST simples com `BasketId` alheio | HTTP 401 e carrinho sem alteração |
| Memos Archive | PATCH de nota privada própria | PATCH de nota privada do outro usuário | Resposta e consulta pelo proprietário confirmam `ARCHIVED` |

O formato com `BasketId` duplicado explora a diferença entre a verificação do
primeiro valor e a gravação do último no Juice Shop. O proxy encaminha os bytes
originais; o parser do evento considera o último valor, que coincide com o
carrinho efetivamente alterado. O teste não troca o corpo encaminhado por uma
versão reserializada.

O Juice Shop pode devolver o erro 401 em texto que não é JSON válido. A captura
preserva esse corpo, e a comparação offline usa a mesma política de parsing do
runtime (`strict_bodies=False`). Não se converte a resposta em um JSON inventado.

Cada nota alvo no Memos é preparada separadamente por ator/proprietário. Isso
permite conferir uma mudança de `NORMAL` para `ARCHIVED`, evitando considerar
uma tentativa sobre nota já arquivada como prova de alteração. A listagem
observada inclui apenas notas privadas do próprio usuário. Notas alheias não
entram nos tokens disponíveis de quem executa o ataque.

Logins, listagens, criação dos objetos, limpeza de itens e consultas de
confirmação são preparação/evidência, fora dos denominadores TP/TN. As interações
necessárias para produzir recursos passam pelo proxy; as consultas de evidência
vão diretamente à aplicação, sem introduzir recursos alheios na CPN do atacante.

## Modelos e identidade

Os modelos são os exemplos reconstruídos do projeto, adaptados às operações
executadas. O modelo Juice Shop combina View e Manipulate Basket e inclui
explicitamente a resposta 401 do POST de itens, para avaliar tentativas negadas.
Esses modelos não são apresentados como OpenAPI originais integralmente iguais
às do autor. Os arquivos exatos são salvos junto ao experimento.

O Juice Shop usa a correlação padrão entre `authentication.token` e
`Authorization`, com hash como identidade. O Memos autentica realmente por cookie;
para associar o evento à sessão do monitor, o alimentador controlado fornece
`X-CPN-Session`, distinto por ator e repetição. Esse cabeçalho é um identificador
experimental, não um mecanismo de autenticação nem uma solução de identidade
pronta para produção.

O runtime continua após divergências, em modo de observação. Alertas BOLA são
as divergências de controle/dados, conforme `cpn_detection.py`. Resultados sem
classificação permanecem explícitos no denominador de cobertura. O objetivo é
detectar tentativas, independentemente de o servidor ter permitido o efeito.

## Resultados das dez repetições

| Cenário | Requisições | TP | FP | FN | TN |
| --- | ---: | ---: | ---: | ---: | ---: |
| Juice Shop — View Basket | 40 | 20 | 0 | 0 | 20 |
| Juice Shop — Manipulate Basket | 40 | 20 | 0 | 0 | 20 |
| Juice Shop — Manipulate negado | 20 | 20 | 0 | 0 | 0 |
| Memos — Archive | 40 | 20 | 0 | 0 | 20 |
| **Total** | **140** | **80** | **0** | **0** | **60** |

Precisão, recall, F1, acurácia e cobertura foram de 100% **nesse conjunto
controlado**. Das 80 tentativas, 60 tiveram efeito confirmado na aplicação e
20 foram negadas pelo próprio Juice Shop. As duas categorias foram detectadas.
Não houve erros de captura ou de transporte no experimento concluído.

Os mesmos eventos capturados foram analisados pelo offline, incremental e
runtime. A comparação confrontou classificações, tipos/mensagens de divergência
e marcações com multiplicidades **após cada evento capturado**, incluindo a
preparação observada. Houve equivalência nos dois aplicativos. As matrizes por
modo estão registradas separadamente.

Saídas em `docs/avaliacao_deteccao_real/`:

- `gabarito.jsonl`: classificação fixada antes de cada chamada alvo.
- `eventos_rotulados.jsonl`: resultado HTTP, decisão do monitor, sucesso da exploração e concordância entre modos.
- `juice.openapi.json` e `memos.openapi.json`: modelos efetivamente usados.
- `resultado.json`: matrizes, métricas, cobertura, versões, adaptações e hashes.
- `README.md`: tabela por cenário.

## Dificuldades e adaptações registradas

1. **Schema do Memos em prod:** o fork declara versão estável 0.15.2, mas o backend
   usa a coluna `resource.memo_id`, presente no schema dev/0.16. O modo prod
   devolveu HTTP 500 ao compor uma nota. A execução final usa o modo dev original,
   com banco limpo e versão reportada 0.16.0, sem patch de código/schema.
2. **Limitador do Memos:** uma rodada sem espaçamento encontrou HTTP 429. A rodada
   final respeita um intervalo mínimo de 50 ms entre chamadas do alimentador,
   abaixo do limite nominal de 30 requisições/s do fork. O limitador permanece
   habilitado. Esse intervalo não é usado como resultado de desempenho.
3. **Resposta não JSON do Juice Shop:** a comparação mantém o mesmo tratamento
   dos corpos observado no runtime, inclusive no HTTP 401.
4. **Artefato Juice Shop:** imagem oficial fixada da mesma versão declarada no
   fork, em vez de recompilar o fork com Winston. A observação externa substitui
   a instrumentação de logs interna, e essa diferença não é omitida.

## O que foi demonstrado e o que não foi

Foi demonstrada a detecção nos cenários selecionados das duas aplicações reais,
com concordância funcional entre os três modos. Isso atende ao experimento de
capacidade de detecção proposto para a etapa 4 nesse recorte.

As dez repetições de cada roteiro não equivalem a 140 ataques/interações
independentes ou a amostragem representativa. Há dois usuários por aplicação e
um conjunto restrito de operações. A avaliação não reproduz o estudo completo
com participantes da tese, nem demonstra ausência de falsos positivos para
todas as políticas de autorização.

Os vinte falsos positivos do laboratório anterior continuam válidos: acesso
compartilhado não modelado e contexto ausente não foram incluídos neste conjunto
privado com preparação completa. Por isso os resultados ficam separados, sem
substituir os percentuais globais do laboratório por 100%.

A etapa 5 continua necessária para medir latência, vazão, CPU/memória e efeito
do crescimento do estado. Esta avaliação usa chamadas sequenciais e não mede
capacidade máxima ou overhead sob concorrência.
