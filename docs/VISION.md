# CardForge-M1 — Subsistema de Visão Computacional

> Parte do software do Raspberry Pi. Visão geral, instalação e main loop: [README](../README.md).

Versão final do reconhecimento de cartas de *Magic: The Gathering* do CardForge-M1, implementada a partir
da prova de conceito (`mtg_scanner.py`) e do design de software do Deliverable 3 (diagramas **CV01–CV16**).

O módulo roda inteiro no Raspberry Pi, sem rede durante a identificação (RNF15). O Scryfall é usado só para
montar/atualizar o espelho local `data/cards.db` (RNF16), fora de uma run.

## Estrutura

```
cardforge/
  cv/                        # pacote cardforge.cv (CV-CL-00)
    vision.py                #   VisionModule: start() · identify() · reload_database() · update_database()
    recognizer.py            #   CardRecognizer.recognize(frame) -> IdentificationResult
    result.py                #   IdentificationResult, IdentificationStatus
    config.py                #   VisionConfig (lido de vision.toml)
    capture/                 #   CameraService (Picamera2, Camera v2 IMX219), RpicamCameraService (rpicam-still), FileCameraService
    calibration.py           #   métricas de calibração, média robusta do recorte e gravação no vision.toml
    detection/               #   fixed.py (recorte fixo calibrado) · ContourCandidateGenerator, QuadrilateralFinder,
                             #   CardDetector (detecção de bordas: só calibração e fotos de outras fontes), PerspectiveRectifier
    ocr/                     #   OcrEngine, OrientationResolver, FaceDownDetector, LayoutClassifier, RoiCatalog, FieldExtractor
    matching/                #   InfoLineParser, SetCodeCatalog, NameIndex, CardMatcher
    report.py, __main__.py   #   relatórios e CLI
  data/                      # pacote cardforge.data (CV-CL-03 / CV-ER)
    repository.py            #   SqliteCardRepository (somente leitura)
    sync.py                  #   ScryfallSyncService (bulk "default_cards" + /sets, troca atômica)
    schema.py                #   schema do cards.db (CV16)
tests/test_vision.py         # 23 testes unitários (sem câmara, rede ou modelos)
vision.toml                  # limiares e parâmetros da câmara
data/cards.db                # espelho local do Scryfall (gerado pelo comando sync, ~40 MB)
```

Os arquivos da PoC (`mtg_scanner.py`, `atualizar_base_local.py`, `atualizar_sets.py`, `mini_scryfall.json`,
`sets_oficiais.json`) estão em [`deprecated/`](../deprecated/), só para referência, e podem ser removidos: o
`cards.db` substitui os dois JSON.

## Instalação

**PC (desenvolvimento):**

```bash
python -m venv venv
venv/Scripts/pip install -r requirements.txt
```

**Raspberry Pi 3B/4B** — Raspberry Pi OS **64-bit** (o onnxruntime só existe para aarch64), Python 3.11:

```bash
sudo apt install -y python3-picamera2
python3 -m venv --system-site-packages venv
venv/bin/pip install -r requirements.txt
```

## Uso

```bash
python -m cardforge.cv sync                    # cria/atualiza data/cards.db (precisa de internet, ~20 s + download)
python -m cardforge.cv identify foto.jpg        # identifica imagens e imprime JSON (--detect: fotos de fora da máquina)
python -m cardforge.cv batch input_cartas --detect --out relatorios   # fotos da PoC: detecção de bordas
python -m cardforge.cv batch fotos_ref --truth gabarito.csv     # fotos da máquina (recorte fixo): acurácia RNF18
python -m cardforge.cv camera -n 5              # no Pi: captura com a câmara e identifica
python -m unittest discover -s tests -v
```

`batch` gera, por imagem, `<foto>_processada.jpg` (carta retificada com as ROIs), `<foto>_dados.txt` e
`<foto>_dados.json`, além de `resultados.csv` e um resumo com a distribuição de status e o tempo por captura.
O gabarito é um CSV com as colunas `file,set,number` (ou `file,name`).

## Integração com o programa de controle (FW-CL-01 / FW-AD-01)

Já implementada em [`sorter_api/identifier.py`](../sorter_api/identifier.py) (`VisionIdentifier`), usada pelo main loop
em [`sorter_api/controller.py`](../sorter_api/controller.py). O esboço abaixo mostra a ideia:

O laço de retentativas e a iluminação pertencem ao `MainLoop`; a visão faz **uma captura por chamada**:

```python
from cardforge.cv import VisionModule, VisionConfig, IdentificationStatus, DatabaseUnavailable

vision = VisionModule(VisionConfig.load("vision.toml"))
try:
    vision.start()                       # modelos ONNX + cards.db + câmara
except DatabaseUnavailable:
    ...                                  # pre-run block: "card database missing" (Start recusado)

def identify_card(a2):                   # chamado com a carta parada pelo gate (A2 CARD_READY)
    level = BASE_LIGHT
    for attempt in range(3):             # 1 + 2 retentativas (RF29)
        result = vision.identify()
        if result.status.is_final:       # IDENTIFIED ou FACE_DOWN
            break
        level = min(level + LIGHT_STEP, 100)
        a2.setLight(level)               # RNF09
    if result.status is IdentificationStatus.IDENTIFIED:
        return sorting_rules.bin_for(result.card), result     # card.color_identity -> RF20
    return 6, result                     # bin 6: não identificada (RF25) ou virada (RF39)
```

`IdentificationResult` traz `status`, `card` (`CardRecord`: `print_id` = id do Scryfall usado como
`card_identifier`, nome, custo, tipo, `color_identity`, raridade, edição, número), `method`, `confidence`,
`needs_review`, `layout`, os campos lidos e `elapsed_ms`. `vision.update_database()` só deve ser chamado sem
run ativa; se falhar, o banco anterior continua em uso.

## Pipeline (uma chamada `identify()`)

1. **Borda e retificação** (CV-AD-02): 5 máscaras (Canny, Canny preenchido, Otsu escuro, Otsu escuro aberto,
   Otsu claro) em 3 passes; quadriláteros com proporção de carta, quase retangulares e sem encostar na borda do
   frame; escolhe o maior (borda externa, não a caixa de arte); perspectiva corrigida para 630×880.
   Falha → `CROP_FAILED`.
2. **Orientação** (CV-AD-03): título lido com as ROIs padrão e pontuado contra os nomes oficiais; se < 75, lê
   a carta girada 180° e só a adota se ganhar por > 10 pontos. Uma linha de info válida (edição oficial + número)
   também confirma a orientação.
3. **Verso da carta** (RF39, novo): nenhum título nas duas orientações **e** ≥ 75 % do interior marrom
   (verso ≈ 85 %, frentes ≤ 67 %) → `FACE_DOWN`.
4. **Atalho**: Nome e Info ficam no mesmo lugar em todos os layouts. Se a leitura da etapa 2 (ROIs padrão) já
   identifica a carta, a captura termina aqui — exceto quando a linha de info marca token (`T0002`) ou quando só
   o nome bateu apesar de set+número lidos. Na maioria das fotos a captura usa **uma única chamada de OCR**.
5. **Layout** (uma chamada de OCR, só se o atalho não resolveu): Standard, Saga, Full Art, Token Full, Token Classic.
6. **Campos** (uma chamada de OCR): Nome, Tipo e Info nas ROIs do layout; reaproveita a leitura da etapa 2
   quando o layout é Standard.
7. **Info** (CV-AD-04): número (`236/249` tem prioridade sobre o ano do copyright) e código da edição (exato;
   idioma colado como em `FICEN`/`TLEENBI`; trocas típicas de OCR como `F1N`→`FIN`, `JKO`→`IKO`).
8. **Match** no `cards.db`: set+número cruzado com o nome → nome exato → nome fuzzy. Divergências ficam com
   `needs_review` abaixo de 90 % e viram `LOW_CONFIDENCE` (o controle recaptura e, no fim, manda para o bin 6).

## Resultados no conjunto de fotos da PoC (63 imagens de `input_cartas`)

| Status | Imagens |
| --- | --- |
| IDENTIFIED | 56 (88,9 %) |
| LOW_CONFIDENCE | 3 (4,8 %) |
| CROP_FAILED | 4 (6,3 %) |
| FACE_DOWN | 0 (nenhum falso positivo) |

- Os 7 exemplos do Deliverable 4 são identificados, incluindo a carta em português *Sagui Espinhoso* → Prickly Marmoset.
- Tempo médio de 0,62 s por captura num PC (Python 3.13, x86-64) — era 1,19 s antes das otimizações abaixo,
  com resultados idênticos nas 63 fotos; **ainda não medido no Raspberry Pi**.

### Desempenho (medido nas 63 fotos, PC)

| Etapa | Tempo |
| --- | --- |
| Detecção de bordas + retificação | ~40 ms (≈ 5 %) |
| OCR (média de 1,2 chamadas por captura) | ~95 % do restante |
| Match no SQLite + fuzzy | ~2 ms |

| Otimização | Efeito | Configuração |
| --- | --- | --- |
| Pular o OCR de layout quando a leitura padrão já identifica | ~−40 %, sem perdas | `skip_layout_when_identified` |
| Classificador de ângulo do RapidOCR desligado | ~−11 %, sem perdas | `ocr_angle_cls` |
| Detector sem ampliar (`"max"`, 960) | ~−20 %, mas 4 cartas a menos confirmadas por set+número | `ocr_det_limit_type/side_len` (padrão mantido) |
| `ocr_upscale = 1.0` | ~−8 %, mas 4 cartas a menos identificadas | `ocr_upscale` (padrão mantido) |
| Ler a faixa de layout junto com Nome/Tipo/Info | sem ganho (o custo é por linha de texto, não por chamada) | descartado |
- As 4 falhas de recorte são cartas pequenas fotografadas à mão com sombra forte sobre fundo claro, situação que
  a câmara escura com iluminação própria (RNF08) elimina. Os 3 `LOW_CONFIDENCE` são duas fotos de uma carta com
  moldura *showcase* (a linha de tipo fica fora da ROI) e um recorte errado.
- O detector de verso foi verificado com fotos sintéticas do verso da carta (→ `FACE_DOWN` nas duas orientações).

O conjunto da PoC não tem gabarito; para medir RNF18 (≥ 90 % em ≥ 100 cartas) e RF17, monte o baralho de
referência, fotografe-o **na máquina** e rode `batch --truth`.

## Onde esta versão difere do design (Deliverable 3)

| Item | Design | Implementado | Motivo |
| --- | --- | --- | --- |
| Cartas não inglesas | `SET_NUMBER_NAME_MISMATCH` → revisão | `SET_NUMBER_LOCALIZED` (90 %) quando o nome não bate com nenhuma carta inglesa e os tipos da linha de tipo (PT/ES/FR/DE/IT) batem com a carta | Senão toda carta em português iria para o bin 6, comprometendo RNF18. Desligável com `accept_localized_cards = false` |
| `max_area_ratio` | 0,60 | 0,85 (configurável) | Nas fotos de teste a carta ocupa até 67 % do frame; recalibrar com o enquadramento final |
| Validação do quadrilátero | proporção | + retangularidade ≥ 0,85, sem encostar na borda do frame, proporção por média dos lados | Evita quads "puxados" até o canto da imagem pela sombra |
| ROIs de Nome/Info | PoC (y 30–85, x 30–300) | Nome y 22–105, Info x 10–320 | O título e o 1.º caractere da edição eram cortados quando o recorte vinha um pouco deslocado |
| Orientação | só título | + linha de info válida | Corrige rotações erradas e evita uma chamada de OCR a mais em cartas não inglesas |
| Nomes indexados | nome + faces | + `flavor_name` (Universes Beyond) | Ex.: *The Mechanist, Tactical Tinkerer* = Lita, Mechanical Engineer |
| Escolha da impressão por nome | mais recente | edição lida pelo OCR primeiro; evita The List / Secret Lair | Inventário mais fiel quando o número não foi lido |
| Classificação de layout | sempre (1 OCR) | só quando a leitura com as ROIs padrão não identificou a carta | ~−40 % de tempo, resultados idênticos nas 63 fotos. Desligável com `skip_layout_when_identified = false` |
| `MatchMethod` | — | + `SET_NUMBER_LOCALIZED` | Rastreabilidade do caso acima |

## Requisitos cobertos por este módulo

| Requisito | Como |
| --- | --- |
| RF15 | `CameraService` (exposição/AWB fixos); nitidez (variância do Laplaciano) em `result.debug["sharpness"]` |
| RF17 | Nome, Edição e Número extraídos por OCR; medir com `batch --truth` |
| RF18 | Confiança ≥ 90 % e sem `needs_review` → `IDENTIFIED`; dados completos (tipo, identidade de cor…) do `cards.db` |
| RF25 / RF29 / RNF09 | Status explícitos por captura; retentativas e luz no programa de controle (exemplo acima) |
| RF39 | `FaceDownDetector` → `FACE_DOWN` |
| RNF15 | Pipeline 100 % local (OpenCV + RapidOCR/ONNX + SQLite); `requests` só é importado pelo `sync` |
| RNF16 | Scryfall como única fonte, via espelho local |
| RNF18 / RNF22 | Ferramentas de medição em `batch` (acurácia com gabarito, tempo por captura) |
| RF33 (opcional) | Não implementado (custo de mana vem do banco, não do OCR) |

## Próximos passos

1. Calibrar `[camera]` e `max_area_ratio` com fotos tiradas **dentro** da câmara de captura.
2. Medir o tempo por captura no Pi 3B (orçamento de 8,6 s por carta, RNF22). Se faltar margem: resolução da
   câmara menor, `ocr_det_limit_type = "max"` (validar a precisão por set+número), Pi 4, cooler contra
   *thermal throttling*.
3. Fotografar o baralho de referência e medir RNF18/RF17 com `batch --truth`.
4. Testar o verso da carta com um verso real fotografado na máquina e, se preciso, ajustar `[face_down]`.
