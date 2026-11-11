# Arquivos legados (prova de conceito da visão computacional)

Nada do software atual usa estes arquivos; eles ficaram aqui só para referência e podem ser apagados.

| Arquivo | O que era | Substituído por |
|---|---|---|
| `mtg_scanner.py` | Prova de conceito da visão: recorte, OCR e busca da carta, num script único | `cardforge/cv/` (`python -m cardforge.cv`) |
| `atualizar_base_local.py` | Baixava o Scryfall e gerava `mini_scryfall.json` | `python -m cardforge.cv sync` → `data/cards.db` |
| `atualizar_sets.py` | Baixava a lista de edições para `sets_oficiais.json` | idem (tabela `mtg_set` do `cards.db`) |
| `mini_scryfall.json` (28 MB) | Banco de cartas da prova de conceito | `data/cards.db` |
| `sets_oficiais.json` | Lista de códigos de edição | `data/cards.db` |

As fotos da prova de conceito continuam em `input_cartas/`, na raiz: os testes e o modo simulado
(`MTG_VISION=files`) as usam.
