# CardForge-M1 — Software do Raspberry Pi

Um único processo no Raspberry Pi integra os quatro componentes do separador de cartas:

```
App mobile (Flutter) ──HTTP──► Raspberry Pi: python -m sorter_api ──RS-485──► A1 (módulo de cima: alimentador, flap)
                     ◄──WS───   main loop + API + visão computacional └──────► A2 (base giratória: 6 bins)
                                   │                    │
                  câmera CSI (Camera v2, IMX219)  data/cards.db (espelho local do Scryfall)
                                                  data/run.db   (runs e cartas separadas)
```

> **Passo a passo completo** — testar no PC sem hardware, preparar o Pi, subir a máquina por etapas, calibrar a
> câmera, medir acurácia e rodar no dia a dia: [docs/PASSO_A_PASSO.md](docs/PASSO_A_PASSO.md).

| Componente | Onde | Documentação |
|---|---|---|
| Main loop + API de controle | [`sorter_api/`](sorter_api/) | [docs/API.md](docs/API.md) |
| Visão computacional | [`cardforge/`](cardforge/) | [docs/VISION.md](docs/VISION.md) |
| Firmware A1 / A2 | [`firmware/`](firmware/) | [docs/PROTOCOL.md](docs/PROTOCOL.md) |
| App mobile | repositório *CardForge App* (sem alterações de código) | [Conectar o app](#conectar-o-app) |

## O main loop

**Start** (`POST /run/start` ou o botão Start do app):
1. Verificações rápidas (< 1 s): banco de cartas carregado, recorte da câmera calibrado, cartas na pilha,
   plataforma livre e os **6 sensores IR dos bins** vazios (RF07).
   Se algo falha, recusa na hora, listando todos os problemas.
2. A2 faz o homing da base enquanto A1 para o rolo, fecha o flap e acende a luz da câmara.
3. Verificações repetidas; cria a run em `run.db`; LED de status em "Running" (verde piscando).

**Para cada carta** (até a pilha esvaziar, um Stop ou 250 cartas — RNF27):

| Passo | Quem | Requisitos |
|---|---|---|
| 1. Alimenta uma carta até o IR da plataforma | A1 (`FEED`) | RF01, RF05, RF09 |
| 2. Foto + identificação, até 3 capturas; luz +20 % antes de cada nova tentativa. A carta é cortada pelo **recorte fixo** calibrado (sem detecção de bordas) | Pi: câmera + `cardforge.cv` | RF15, RF17, RF18, RF29, RNF09, RNF15 |
| 3. Bin pela identidade de cor: W→1, U→2, B→3, R→4, G→5; incolor, multicolor, não identificada e virada → 6 | Pi: [`sorting.py`](sorter_api/sorting.py) | RF20, RF25, RF39 |
| 4. Gira a base até o bin | A2 (`GOTO`) | RF03 |
| 5. Abre o flap, espera a carta cair, fecha | A1 (`FLAP`) | RNF04 |
| 6. Um registro em `run.db`, em uma transação, depois da queda física | Pi: [`store.py`](sorter_api/store.py) | RF19, RNF19, RNF20 |
| 7. Evento de progresso (sem identidade nem bin) e, se for o caso, alerta de atenção | WebSocket | RF23, RF36 |

**Falhas** (RNF23, FW-SD-04): atolamento, carta que não cai ou base fora de posição **pausam** a run: motores parados,
LED vermelho, evento `fault` para o app (RF27). O usuário resolve e aperta **Resume**: a base faz homing de novo
e a **mesma** run continua. Módulo que para de responder (perda do link) encerra a run como `failed` (exige
desligar e religar). O botão de emergência corta a energia no hardware (RF14).

**Stop**: termina a carta em andamento — se já foi identificada vai para o seu bin, senão para o bin 6 — registra,
fecha o flap e para os motores. Nenhuma carta fica na máquina.

**Armazenamento**: as fotos de cada carta ficam só na memória; no cartão SD vai apenas a linha da carta em
`run.db`. Ao fim de cada run, o serviço esvazia as pastas temporárias (`data/tmp`, `capturas`, `relatorios` — as
saídas dos comandos de bancada), apaga downloads interrompidos (`data/*.tmp`) e compacta o journal do `run.db`. O
código, o `vision.toml`, o `cards.db`, o `run.db` e as fotos de `calibragem_recorte/` nunca são apagados.

**Resultados** (RF22): só depois do fim da run, em `GET /runs/{id}` (contagem por bin + todas as cartas);
inventário em `GET /cards` (app) e `GET /inventory/export.csv` (RF24, RFC 4180).

## Instalação no Raspberry Pi

1. **Sistema**: Raspberry Pi OS **64-bit** (o onnxruntime só existe para aarch64). Em `raspi-config`: habilite a
   câmera e a UART, e desligue o console serial.
2. **Código e dependências**:
   ```bash
   git clone <este repositório> /home/pi/CardForge-M1 && cd /home/pi/CardForge-M1
   sudo apt install -y python3-picamera2 python3-gpiozero python3-lgpio
   python3 -m venv --system-site-packages .venv
   .venv/bin/pip install -r requirements.txt
   ```
3. **Banco de cartas** (uma vez, precisa de internet; depois tudo funciona offline):
   ```bash
   .venv/bin/python -m cardforge.cv sync
   ```
4. **Firmware**: grave `firmware/a1_top/a1_top.ino` no Arduino de cima e `firmware/a2_base/a2_base.ino` no da base
   (detalhes de pinos em [docs/PROTOCOL.md](docs/PROTOCOL.md)).
5. **Teste manual**:
   ```bash
   MTG_VISION=camera MTG_SERIAL_PORT=/dev/ttyUSB0 .venv/bin/python -m sorter_api
   ```
   Abra `http://<ip-do-pi>:8000/docs`. (Conversor USB-RS485, como no Deliverable 5. Com um MAX485 na UART do GPIO:
   `MTG_SERIAL_PORT=/dev/serial0 MTG_DE_PIN=<pino BCM do DE+RE>`.)
6. **Serviço no boot**: ajuste e instale [`deploy/sorter-api.service`](deploy/sorter-api.service) (instruções no arquivo).

### Calibração da câmera com a máquina montada

Não é preciso abrir a câmara de leitura nem tirar o Raspberry Pi: o serviço tem uma página de calibração.
Com o serviço rodando (`MTG_VISION=camera`) e nenhuma run ativa, abra no celular ou notebook, na mesma rede:

```
http://<ip-do-pi>:8000/calibration
```

Sem acesso direto à rede do Pi, faça um túnel SSH e abra `http://localhost:8000/calibration`:

```bash
ssh -L 8000:localhost:8000 pi@<ip-do-pi>
```

Procedimento sugerido:

0. **Recorte fixo** (uma vez, e de novo se a câmera ou a plataforma mudarem de lugar): **Alimentar 1 carta** →
   **Medir posição desta carta** → **Descartar carta (bin 6)**, com 4 ou 5 cartas; **Salvar recorte fixo**. A média
   das posições vira o recorte usado em todas as fotos, sem detecção de bordas. A página mostra a *variação entre as
   cartas* (até ~15 px é tranquilo) e, a cada captura, o *desvio* da carta atual em relação ao recorte (verde =
   borda detectada agora, laranja = recorte salvo). Sem recorte salvo, o Start é recusado com `CROP_NOT_CALIBRATED`.
1. **Cartão branco**: coloque um cartão branco liso do tamanho de uma carta no topo da pilha e toque em
   **Alimentar 1 carta** (o alimentador o leva até a plataforma, o flap o segura).
2. **Autocalibrar (exposição + branco)**: a câmera ajusta exposição e balanço de branco sozinha com o cartão
   branco e os valores ficam travados, como numa run. Confira em **Medidas** a *luz desigual* (≤ 20 %) e os
   *pixels estourados* (≤ 2 %); se a luz estiver desigual, a posição/difusão das fitas LED precisa de ajuste.
3. **Descartar carta (bin 6)** e alimente cartas reais. Use **Capturar** (ou **Ao vivo**) e ajuste exposição,
   ganho e luz até o *brilho da carta* ficar entre 60 e 180 e a *nitidez do texto pequeno* acima de 70.
   Nitidez baixa com a carta parada = foco: a lente da Camera Module v2 é girada à mão (com a ferramenta que
   acompanha a câmera); a página só mede.
4. **Identificar a carta** mostra a leitura completa (OCR, edição, número, carta encontrada) com a imagem anotada.
5. **Luz ambiente × escuro (RNF08)**: **Marcar referência**, acenda/apague a luz da sala, **Capturar**: a
   variação deve ficar em até 10 %.
6. **Salvar no vision.toml** grava exposição, ganho, branco e luz na seção `[camera]` (valem após reiniciar).

As medidas e orientações da página também indicam se a carta ocupa uma área do quadro compatível com
`max_area_ratio` / `min_area_ratio` do `vision.toml`. As referências (brilho 60–180, nitidez ≥ 70) vieram das
fotos da prova de conceito; refine-as com o benchmark das fotos feitas na câmara (`python -m cardforge.cv batch`).

**Câmera: Pi Camera Module v2 (IMX219).** No Raspberry Pi OS Bookworm ela é detectada sozinha
(`camera_auto_detect=1` em `/boot/firmware/config.txt`). Se esse arquivo ainda tiver `dtoverlay=ov5647`, da câmera
anterior, troque por `dtoverlay=imx219` (ou apague a linha e deixe a detecção automática). `rpicam-hello --list-cameras`
(com o serviço parado) deve listar `imx219`. O serviço avisa no log se a câmera encontrada não for a do `vision.toml`,
e a página de calibração mostra o sensor detectado e usa os limites dele (ganho analógico da IMX219: 1 a ~10,7).
A resolução padrão é **1640×1232** (modo 2×2 do sensor, campo de visão inteiro); `[3280, 2464]` (8 MP) só vale a
pena se a carta ocupar pouco do quadro.

**Biblioteca da câmera.** O código usa o **Picamera2** (pacote `python3-picamera2`), a biblioteca Python oficial da
Raspberry Pi, que roda sobre a pilha libcamera — a mesma das ferramentas `rpicam-*`. Ele não chama os antigos
`libcamera-still` / `libcamera-hello` (renomeados para `rpicam-still` / `rpicam-hello` no Raspberry Pi OS
Bookworm) nem o `raspistill`. Como alternativa, `[camera] backend = "rpicam"` no `vision.toml` faz cada foto com
`rpicam-still` (mais lento, ~1 s por foto). Enquanto o serviço está rodando, ele mantém a câmera aberta, então
`rpicam-hello` / `rpicam-still` só funcionam com o serviço parado (`sudo systemctl stop sorter-api`); por isso
a calibração é feita pela página.

### Configuração (variáveis `MTG_*`)

As da comunicação RS-485 e da mecânica estão em [`sorter_api/config.py`](sorter_api/config.py) e em
[docs/API.md](docs/API.md). As novas:

| Variável | Padrão | O que faz |
|---|---|---|
| `MTG_VISION` | `off` | `camera` = máquina real · `files` = visão real sobre fotos de `MTG_VISION_IMAGES` (bancada / simulador) · `off` = sem visão, bins 1→6 em ciclo (teste da mecânica) |
| `MTG_VISION_CONFIG` | `vision.toml` | Limiares da visão, câmera e caminho do `cards.db` |
| `MTG_VISION_IMAGES` | `input_cartas` | Fotos usadas com `MTG_VISION=files`, em ordem alfabética |
| `MTG_IDENTIFY_ATTEMPTS` | `3` | Capturas por carta (1 + 2 retentativas, RF29) |
| `MTG_LIGHT_BASE_LEVEL` / `MTG_LIGHT_STEP` | `[camera] light_level` / `20` | Luz da câmara em %, e o aumento a cada retentativa (RNF09). Sem a variável, vale o nível salvo pela calibração |
| `MTG_MAX_CARDS_PER_RUN` | `250` | Fim automático da run (RNF27) |
| `MTG_RUN_DB` | `data/run.db` | Banco local das runs |
| `MTG_APP_USERNAME` / `MTG_APP_PASSWORD` | `admin` / *(vazia)* | Login do app. Senha vazia = qualquer credencial é aceita (só para testes) |
| `MTG_CROP_CAPTURES_DIR` | `calibragem_recorte` | Onde ficam as fotos usadas para calibrar o recorte fixo (nunca apagadas automaticamente) |
| `MTG_CLEANUP_DIRS` | `data/tmp,capturas,relatorios` | Pastas esvaziadas ao fim de cada run (`""` = não limpar) |
| `MTG_LED` | `auto` | LED de status no GPIO do Pi: `auto` (GPIO no Pi, em memória com `--simulate`), `gpio`, `off` |
| `MTG_LED_PINS` | `17,22,27` | GPIO (BCM) do vermelho, verde e azul = pinos físicos 11, 15 e 13; catodo comum no pino 9 |
| `MTG_NETWORK_MONITOR` | `auto` | Mostra Setup / No Network (azul) consultando o NetworkManager (`nmcli`); `on` / `off` |

### LED de status (RF16, RF41)

LED RGB de catodo comum ligado ao Raspberry Pi: COM → pino 9 (GND), vermelho → pino 11 (GPIO 17), azul → pino 13
(GPIO 27), verde → pino 15 (GPIO 22). Quem o controla é o próprio serviço ([`sorter_api/status_led.py`](sorter_api/status_led.py)),
seguindo o Apêndice B dos requisitos:

| Estado | LED | Quando |
|---|---|---|
| Idle | verde fixo | ligado e pronto, run terminada |
| Running | verde piscando 1 Hz | run em andamento |
| Feed Jam | vermelho fixo | run pausada por falha (atolamento, carta presa, base); também módulo sem resposta |
| Startup Blocked | amarelo fixo | Start recusado (bin com carta, pilha vazia...), até o próximo comando |
| Setup | azul fixo | nenhuma rede Wi-Fi configurada ainda (configuração por Bluetooth) |
| No Network | azul piscando 1 Hz | Wi-Fi configurado mas desconectado |
| Emergency Stop / Double-Feed / Paused – Connection Lost | vermelho 4 Hz / vermelho 2 Hz / amarelo 1 Hz | implementados, mas não acontecem: o botão de emergência corta a energia do Pi, RF06 não foi construído, a identificação é offline |

Os estados de rede (azul) só aparecem com a máquina parada; o estado da máquina tem prioridade.

## Sem hardware (PC)

```bash
pip install -r requirements.txt
MTG_VISION=files python -m sorter_api --simulate     # Arduinos simulados + visão real sobre input_cartas/
python -m pytest -q                                  # 106 testes (o pi_ble_setup tem os seus: cd pi_ble_setup && python -m pytest tests)
```

A página de calibração também funciona assim (`http://localhost:8000/calibration`): a "câmera" mostra as fotos e
simula os ajustes de exposição, ganho e branco. O simulador segue o mesmo protocolo dos Arduinos. `PUT /sim/state` coloca cartas na pilha, enche bins, trava o
alimentador ou desliga um módulo, para testar as telas de erro do app.

## Conectar o app

O app já fala com `API_BASE_URL` usando as rotas do seu `api_contract.dart`. Como o servidor CardForge ainda não
existe, o Pi responde essas rotas ([`app_compat.py`](sorter_api/app_compat.py)): login, `/cards`,
`/machines/{id}/status|start|stop`. No *CardForge App*, crie o `.env`:

```
USE_MOCK=false
USE_MOCK_BLUETOOTH=true
API_BASE_URL=http://<ip-do-pi>:8000
```

e rode `flutter run --dart-define-from-file=.env`. Entre com o usuário `admin` e a senha de `MTG_APP_PASSWORD`.
No app, **Start/Resume** inicia uma run ou retoma uma run pausada (o app mostra a pausa como "stopped"), e
**Stop** encerra a run. **Cards in folders** lista as cartas separadas, por bin.

O código do app não foi alterado (não há Flutter neste ambiente para compilar e testar). Faltam no app, e a API já oferece:

| No app | Requisito | Já disponível no Pi |
|---|---|---|
| Alertas de falha e de atenção | RF27, RF36 | WebSocket `/ws/events`: eventos `fault` e `attention` |
| Tela de resultados da run | RF22 | `GET /runs/{id}` |
| Exportar CSV | RF24 | `GET /inventory/export.csv` |
| Mostrar o motivo de um Start recusado (hoje toda resposta 409 aparece como "máquina offline") | RF26 | corpo da resposta 409: `problems` |
| Login com Google | — | precisa do servidor CardForge (o Pi responde 501) |

## Diferenças em relação ao design do Notion

O código pronto (firmware e API) é a referência; o design do Deliverable 3 foi seguido onde não conflita.

| Design (Deliverable 3) | Implementado | Por quê |
|---|---|---|
| Modbus RTU sobre UART, A1 = base, A2 = alimentador | RS-485 com protocolo ASCII próprio; **A1 = módulo de cima, A2 = base** | É o que o firmware e a API usam e já foi testado na máquina (Deliverable 5) |
| App ↔ servidor (auth, inventário) e app ↔ Pi (controle) | O Pi responde as rotas do servidor que o app usa | O servidor não existe ainda; `run.db` guarda `sync_state` para o futuro SyncWorker (DB-SD-01) |
| Falha encerra a run (API original) | Falha mecânica **pausa**; Resume continua a mesma run | RNF23 e FW-SD-04 |
| Eventos com bin e id da carta (API original) | Só progresso durante a run | RF23 |
| Parada joga a carta no bin que estiver embaixo (API original) | Vai para o bin dela ou para o 6, e é registrada | RF25, RNF20 |

## Situação dos requisitos de software

| Requisito | Situação |
|---|---|
| RF05, RF08, RF09, RF19, RF20, RF22–RF23, RF25–RF27, RF29, RF39, RNF15, RNF16, RNF19, RNF20, RNF23, RNF27 | Implementados e cobertos por testes com o simulador |
| RF17, RF18, RNF18, RNF22 | Implementados; **medir na máquina** (a visão leva ~4,5 s por captura no Pi, segundo o Deliverable 5) |
| RF16, RF32, RF37, RF41 (LED de status) | Implementados no Pi (GPIO 17/22/27) |
| RNF09 (luz da câmara) | Lado do Pi pronto; **falta o comando `LIGHT` no `a1_top.ino`** ([PROTOCOL.md 5.1](docs/PROTOCOL.md)). Até lá o Pi desliga esse recurso sozinho |
| RF07, RF12, RF13 (sensores IR dos bins) | Implementados: `a2_base.ino` lê os 6 IR (D10, D8, D11, D7, D9, D12) com filtro; o Pi só os consulta antes de iniciar |
| RF24, RF36 | API pronta; falta a tela no app |
| RF40 (Wi-Fi por Bluetooth) | Serviço `pi_ble_setup/` + app (`cardforgeapp/`) |
| RF21, RF35, RF06/RF37, RF30 | Opcionais não implementados (RF30 não se aplica: a identificação é offline) |

## Estrutura

```
sorter_api/                 main loop + API (FastAPI, uvicorn)
  controller.py             estados, start/resume/stop, o loop por carta, pausa em falha
  identifier.py             VisionIdentifier (câmera + visão, retentativas) / PlaceholderIdentifier
  sorting.py                bin pela identidade de cor (RF20)
  store.py                  run.db: local_run, local_cards, tokens do app
  app_compat.py             rotas que o app mobile usa
  calibration_api.py        calibração da câmera pela rede (+ static/calibration.html)
  status_led.py             LED de status RGB no GPIO do Pi e estado da rede
  housekeeping.py           fotos da calibração do recorte e limpeza do SD ao fim de cada run
  api.py, events.py         REST + WebSocket
  modules.py, bus.py, protocol.py, sim.py   A1/A2, RS-485, simulador
cardforge/                  visão computacional (cv; recorte fixo em cv/detection/fixed.py, calibração em
                            cv/calibration.py) e espelho do Scryfall (data)
firmware/                   a1_top, a2_base e testes de bancada da base
deploy/sorter-api.service   serviço systemd
docs/                       PASSO_A_PASSO.md, API.md, PROTOCOL.md, VISION.md
tests/                      test_api, test_protocol, test_integration, test_vision, test_calibration,
                            test_fixed_crop, test_status_led, test_housekeeping
deprecated/                 prova de conceito da visão (só referência, pode ser apagada)
calibragem_recorte/         fotos da calibração do recorte (criada pelo serviço; fora do git)
vision.toml, data/cards.db  configuração e banco de cartas da visão
```
