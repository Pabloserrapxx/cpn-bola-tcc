# Comparação executada com Links2CPN

Perfil da implementação do TCC: `strict`.

Referência: [d6092c3d85ad](https://github.com/ailton07/openapi-links-to-CPNs/tree/d6092c3d85adaf96a0e83a90a3bd2d287cd51325). Código original sem alterações; somente a geração de PNG foi substituída por observação das marcações.

| Caso | Original completou | TCC completou | Falhas original | Falhas TCC | Primeiro erro igual | Desfechos iguais no arquivo inteiro | Marcações comparadas / diferentes* |
| --- | --- | --- | --- | --- | --- | --- | --- |
| [original-contas](original-contas.json) | sim | sim | [] | [2] | não | não | 0 / [] |
| [original-view-legitimo](original-view-legitimo.json) | sim | sim | [] | [2] | não | não | 0 / [] |
| [original-view-multiusuario](original-view-multiusuario.json) | sim | sim | [] | [3, 4] | não | não | 0 / [] |
| [original-view-ataque](original-view-ataque.json) | sim | não | [29] | [] | — | — | 0 / [] |
| [original-manipulate](original-manipulate.json) | sim | sim | [21] | [11] | não | não | 0 / [] |
| [original-experimento](original-experimento.json) | sim | não | [47, 49, 54, 55, 96, 97, 105, 119, 133] | [] | — | — | 0 / [] |
| [original-memos-legitimo](original-memos-legitimo.json) | sim | sim | [] | [9] | não | não | 0 / [] |
| [original-memos-ataque](original-memos-ataque.json) | sim | sim | [11] | [9] | não | não | 0 / [] |
| [normal](normal.json) | sim | sim | [] | [] | sim | sim | 3 / [] |
| [sem-produtor](sem-produtor.json) | sim | sim | [1] | [1] | sim | sim | 0 / [] |
| [id-alheio](id-alheio.json) | sim | sim | [2] | [2] | sim | sim | 1 / [] |
| [apos-erro](apos-erro.json) | sim | sim | [2] | [2] | sim | não | 1 / [] |
| [usuarios](usuarios.json) | sim | sim | [] | [] | sim | sim | 4 / [] |
| [multiplicidade](multiplicidade.json) | sim | sim | [] | [] | sim | sim | 3 / [] |
| [pos-condicao](pos-condicao.json) | sim | sim | [] | [2] | não | não | 1 / [] |
| [json-pointer](json-pointer.json) | sim | sim | [1, 2] | [] | não | não | 0 / [] |
| [guarda](guarda.json) | sim | sim | [] | [2] | não | não | 1 / [] |
| [lista-dois](lista-dois.json) | sim | sim | [] | [] | sim | sim | 2 / [] |
| [lista-um](lista-um.json) | sim | sim | [2] | [] | não | não | 1 / [1] |
| [lista-vazia](lista-vazia.json) | sim | sim | [1] | [] | não | não | 0 / [] |
| [lista-dois-destinos](lista-dois-destinos.json) | sim | sim | [2] | [] | não | não | 1 / [1] |

*Somente eventos disparados por ambos e a projeção de recursos descrita no manifest. Não há comparação de marcação após eventos que um dos programas não processou.

“Completou” significa que o programa terminou sem exceção externa; pode ter encontrado divergências. Uma lista vazia de falhas não significa êxito quando “completou” é não.

Veja [a interpretação metodológica](../comparacao_links2cpn.md) e [o manifest](manifest.json).
