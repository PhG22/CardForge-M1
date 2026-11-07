# Passo a passo: testar e rodar o CardForge-M1

Do zero até a máquina separando cartas: primeiro no PC, sem hardware (parte A); depois no Raspberry Pi, um
componente de cada vez (partes B a F). Cada passo diz o que deve acontecer, para saber se está tudo certo antes de
seguir.

`<ip-do-pi>` é o endereço do Raspberry Pi na rede (`hostname -I` no Pi mostra).

---

## 0. Onde está cada parte

| Parte | Onde | O que tem |
|---|---|---|
| **Main loop + API de controle** | repositório `CardForge-M1`, pasta [`sorter_api/`](../sorter_api/) | |
| | `controller.py` | o loop por carta, Start/Resume/Stop, pausa em falha, limite de 250 cartas |
| | `identifier.py` | câmera + visão, até 3 capturas por carta, luz a cada nova tentativa |
| | `sorting.py` | bin pela identidade de cor (W→1, U→2, B→3, R→4, G→5, resto→6) |
| | `store.py` | `data/run.db`: runs, uma linha por carta separada, tokens do app |
| | `api.py`, `events.py` | rotas REST e WebSocket `/ws/events` |
| | `app_compat.py` | rotas que o app mobile já usa (`/auth/login`, `/cards`, `/machines/...`) |
| | `calibration_api.py`, `static/calibration.html` | calibração da câmera pela rede |
| | `modules.py`, `bus.py`, `protocol.py` | comandos do A1/A2 e o barramento RS-485 |
| | `sim.py` | A1 e A2 simulados (testes e uso sem hardware) |
| | `config.py` | todas as variáveis `MTG_*` |
| **Visão computacional** | [`cardforge/cv/`](../cardforge/cv/) | recorte, OCR, identificação ([docs/VISION.md](VISION.md)); `capture/camera.py` (Picamera2 / rpicam-still), `calibration.py` (medidas) |
| **Banco de cartas** | [`cardforge/data/`](../cardforge/data/), `data/cards.db` | espelho local do Scryfall, criado por `python -m cardforge.cv sync` |
| **Configuração da visão e da câmera** | [`vision.toml`](../vision.toml) | limiares, câmera (IMX219), luz; a calibração grava aqui |
| **Firmware** | [`firmware/`](../firmware/) | `a1_top` (módulo de cima: rolo, flap, IR da pilha e da plataforma), `a2_base` (base: motor, homing); `a2_homing_test` e `a2_tray_move_test` são testes de bancada só da base |
| **Protocolo RS-485** | [docs/PROTOCOL.md](PROTOCOL.md) | quadros, comandos, códigos de erro; 5.1 = comandos `LIGHT`/`LED` que faltam no firmware |
| **Referência da API** | [docs/API.md](API.md) | cada rota, resposta e evento |
| **Serviço no boot** | [`deploy/sorter-api.service`](../deploy/sorter-api.service) | unidade systemd |
| **Testes** | [`tests/`](../tests/) | `test_vision` (visão), `test_protocol` (quadros), `test_api` (API + simulador), `test_integration` (loop completo, persistência, app, visão real em fotos), `test_calibration` |
| **App mobile** | pasta `CardForge App` (projeto Flutter separado) | sem alterações; aponta para o Pi pelo `.env` |
| **Prova de conceito antiga** | `mtg_scanner.py`, `atualizar_*.py`, `*.json` na raiz | só referência; pode ser apagada |

---

## Parte A — No PC, sem hardware

### A1. Instalar

Python 3.11 ou mais novo. Na pasta do repositório:

```bash
python -m venv .venv
```

Windows: `.venv\Scripts\activate`. Linux/macOS: `source .venv/bin/activate`. Depois:

```bash
pip install -r requirements.txt
```

O `data/cards.db` já vem no repositório. Para atualizá-lo (precisa de internet, ~1 min):

```bash
python -m cardforge.cv sync
```

### A2. Rodar os testes

```bash
python -m pytest -q
```

**Esperado:** `88 passed`. Os testes usam os Arduinos simulados; `test_integration` e `test_calibration` também rodam
a visão de verdade sobre fotos de `input_cartas/`.

### A3. Subir o sistema completo simulado

Arduinos simulados + visão real lendo as fotos de `input_cartas/` (uma foto por "carta" alimentada):

```bash
MTG_VISION=files MTG_SIM_STACK_CARDS=5 python -m sorter_api --simulate
```

No Windows (PowerShell), defina as variáveis antes: `$env:MTG_VISION="files"; $env:MTG_SIM_STACK_CARDS="5"`.

**Esperado:** a última linha do log é `sorter API ready (SIMULATED hardware, vision=files)`.

### A4. Acompanhar uma run

Em outro terminal, abra o WebSocket de eventos (o pacote `websockets` vem com o `uvicorn[standard]`):

```bash
python -m websockets ws://localhost:8000/ws/events
```

No terceiro terminal, inicie a run:

```bash
curl -X POST localhost:8000/run/start
```

**Esperado no WebSocket:** `run_started`, cinco `card_sorted` (só com o número da carta, nunca o nome nem o bin —
RF23), talvez `attention` (carta não identificada) e `run_finished`. Depois:

```bash
curl localhost:8000/runs
```

```bash
curl localhost:8000/runs/<run_id>
```

```bash
curl -o inventario.csv localhost:8000/inventory/export.csv
```

`/runs/<run_id>` traz a contagem por bin e cada carta (nome, edição, número, cor, método, confiança). O estado da
máquina simulada fica em `http://localhost:8000/sim/state`. Todas as rotas podem ser testadas pelo navegador em
`http://localhost:8000/docs`.

### A5. Testar falhas, Resume e Stop

Atolamento do alimentador:

```bash
curl -X PUT localhost:8000/sim/state -H "content-type: application/json" -d '{"stack_cards": 5, "trays_with_cards": [], "top_jammed": true}'
```

```bash
curl -X POST localhost:8000/run/start
```

**Esperado:** evento `fault` com `FEED_TIMEOUT`; `curl localhost:8000/run/status` mostra `"state": "paused"`.
Destrave e retome:

```bash
curl -X PUT localhost:8000/sim/state -H "content-type: application/json" -d '{"top_jammed": false}'
```

```bash
curl -X POST localhost:8000/run/resume
```

**Esperado:** `run_resumed` e a **mesma** run (mesmo `run_id`) termina com `completed`. Para testar o Stop, inicie
uma run (esvazie os bins antes com `{"trays_with_cards": []}`) e chame `curl -X POST localhost:8000/run/stop`:
a carta em andamento é separada e registrada, e a run termina `cancelled`.

Outras recusas do Start para testar: `{"stack_cards": 0}` (STACK_EMPTY), `{"trays_with_cards": [2]}`
(TRAYS_NOT_EMPTY), `{"platform_has_card": true}` (PLATFORM_NOT_CLEAR), `{"offline_modules": ["A2"]}` (503).

### A6. Testar a página de calibração

Com o mesmo servidor do A3, abra `http://localhost:8000/calibration`. Aqui a "câmera" são as fotos, e os ajustes de
exposição, ganho e branco são simulados. Teste **Alimentar 1 carta**, **Capturar**, **Identificar a carta**,
**Aplicar**, **Autocalibrar** e **Descartar carta (bin 6)**.
**Salvar no vision.toml** grava de verdade no arquivo indicado por `MTG_VISION_CONFIG` (padrão `vision.toml`).

### A7. Testar o app contra o PC

No `CardForge App`, crie o `.env` apontando para o IP do PC (o celular/emulador precisa alcançá-lo; no emulador
Android o PC é `10.0.2.2`):

```
USE_MOCK=false
USE_MOCK_BLUETOOTH=true
API_BASE_URL=http://<ip-do-pc>:8000
```

```bash
flutter run --dart-define-from-file=.env
```

Entre com usuário `admin` e qualquer senha (sem `MTG_APP_PASSWORD`, qualquer senha é aceita). Vincule a máquina
simulada, abra **Machine control** e use **Start/Resume** e **Stop**; depois **Cards in folders** lista as cartas
separadas por bin.

---

## Parte B — Preparar o Raspberry Pi

### B1. Sistema

1. Raspberry Pi OS **64-bit** (Bookworm ou mais novo). O onnxruntime não existe para 32 bits.
2. Câmera: a Camera Module v2 (IMX219) é detectada sozinha. Em `/boot/firmware/config.txt` deve haver
   `camera_auto_detect=1`; se houver `dtoverlay=ov5647` (câmera antiga), apague a linha ou troque por
   `dtoverlay=imx219`. Reinicie.
3. Confira a câmera (com o serviço ainda não instalado):
   ```bash
   rpicam-hello --list-cameras
   ```
   **Esperado:** uma linha com `imx219`.
4. Barramento: com o conversor **USB-RS485**, confira que ele aparece:
   ```bash
   ls /dev/ttyUSB*
   ```
   Com um MAX485 na UART do GPIO: em `sudo raspi-config` → *Interface Options* → *Serial Port*: login shell **não**,
   hardware serial **sim**.

### B2. Código

```bash
git clone <url-do-repositório> /home/pi/CardForge-M1
```

```bash
cd /home/pi/CardForge-M1
```

```bash
sudo apt install -y python3-picamera2 python3-gpiozero
```

```bash
python3 -m venv --system-site-packages .venv
```

```bash
.venv/bin/pip install -r requirements.txt
```

```bash
.venv/bin/python -m cardforge.cv sync
```

O último comando cria/atualiza o `data/cards.db` (precisa de internet só agora; depois tudo funciona offline).

```bash
.venv/bin/python -m pytest -q
```

**Esperado:** `88 passed` também no Pi (mais lento).

### B3. Firmware (Arduino IDE, no PC)

1. `firmware/a1_top/a1_top.ino` → Arduino do **módulo de cima** (endereço A1). Pinos padrão: rolo D9, flap D10,
   IR da plataforma D6, IR da pilha D5, RS-485 em D4 (RO) / D3 (DI) / D13 (DE+RE).
2. `firmware/a2_base/a2_base.ino` → Arduino da **base** (endereço A2). Pinos: STEP D5, DIR D6, SLEEP D4, RESET D3,
   M2 D2, IR do homing A5, RS-485 em A0 (RO) / A1 (DI) / D13 (DE+RE).
3. Os dois a 9600 baud (igual a `MTG_BAUDRATE`). Os pinos estão no começo de cada `.ino`; mude lá se a fiação
   for diferente.

Para isolar problemas só da base, antes do A2: `a2_homing_test.ino` (homing e repetibilidade) e
`a2_tray_move_test.ino` (todos os bins + perda de passos). Eles mandam o resultado como texto pelo RS-485:
`python -m serial.tools.miniterm /dev/ttyUSB0 9600` no Pi (com a API parada).

---

## Parte C — Subir na máquina, por etapas

Em todas as etapas, a API sobe com o mesmo comando, mudando só `MTG_VISION` e `--top-only`. Exemplo (conversor
USB-RS485):

```bash
MTG_SERIAL_PORT=/dev/ttyUSB0 MTG_VISION=off .venv/bin/python -m sorter_api
```

Use `MTG_LOG_LEVEL=DEBUG` para ver cada quadro RS-485 trocado. A interface de teste fica em
`http://<ip-do-pi>:8000/docs`.

### C1. Comunicação

```bash
curl <ip-do-pi>:8000/hardware/status
```

**Esperado:** `"top": {"online": true, ...}` e `"bottom": {"online": true, ...}`. Coloque e tire cartas da pilha
e confira `stack_has_card` mudando. Se um módulo aparece `online: false`: alimentação, cabo A/B, endereço e baud.

### C2. Só o módulo de cima (se a base ainda não estiver pronta)

```bash
MTG_SERIAL_PORT=/dev/ttyUSB0 MTG_VISION=off .venv/bin/python -m sorter_api --top-only
```

```bash
curl -X POST <ip-do-pi>:8000/run/start
```

**Esperado:** cada carta é alimentada até a plataforma, espera 2 s e cai; ao esvaziar a pilha, a run termina.

### C3. Base

```bash
curl -X POST <ip-do-pi>:8000/base/home
```

```bash
curl -X POST <ip-do-pi>:8000/base/goto/1
```

Teste os bins de 1 a 6 e depois `base/home` de novo: `drift` entre 0 e ±5 = sem perda de passos.

### C4. Mecânica completa, sem câmera

Com `MTG_VISION=off`, inicie uma run: as cartas vão para os bins 1, 2, 3 ... 6, 1 ... em sequência. Serve para
validar alimentação, giro da base e queda (RF01, RF03) sem depender da visão.

---

## Parte D — Calibrar a câmera (máquina montada)

Suba com a câmera:

```bash
MTG_SERIAL_PORT=/dev/ttyUSB0 MTG_VISION=camera .venv/bin/python -m sorter_api
```

e abra `http://<ip-do-pi>:8000/calibration` no celular ou notebook. Sem acesso à rede do Pi, use um túnel e abra
`http://localhost:8000/calibration`:

```bash
ssh -L 8000:localhost:8000 pi@<ip-do-pi>
```

1. **Cartão branco** liso do tamanho de uma carta no topo da pilha → **Alimentar 1 carta**.
2. **Autocalibrar (exposição + branco)**. Em **Medidas**: *luz desigual* ≤ 20 % e *pixels estourados* ≤ 2 %.
   Fora disso, ajuste a posição/difusão das fitas LED (essa é a única parte física).
3. **Descartar carta (bin 6)**, ponha cartas comuns na pilha e **Alimentar 1 carta**. **Capturar** (ou **Ao vivo**):
   *brilho da carta* entre 60 e 180, *nitidez do texto pequeno* acima de 70, *carta encontrada: sim*. Ajuste
   exposição, ganho e luz e **Aplicar**.
4. **Identificar a carta** em algumas cartas diferentes (comuns, foils, tokens, em português, de cabeça para baixo,
   um verso): confira a leitura e a carta encontrada.
5. **Luz ambiente × escuro**: **Marcar referência**, acenda/apague a luz da sala, **Capturar** — até 10 % (RNF08).
6. **Salvar no vision.toml**. Os valores valem a partir de agora e depois de reiniciar.

Notas: o foco da Camera v2 é ajustado girando a lente (a página só mede a nitidez); o ajuste de luz só tem efeito
quando o firmware do A1 tiver o comando `LIGHT` ([PROTOCOL.md 5.1](PROTOCOL.md)); mudar `resolution` no
`vision.toml` exige reiniciar o serviço. Os comandos `rpicam-*` não abrem a câmera enquanto o serviço roda.

---

## Parte E — Medir acurácia e tempo (RNF18, RNF22) com fotos da máquina

### E1. Fotografar o baralho de referência pela própria máquina

Com o serviço rodando (`MTG_VISION=camera`), a pilha carregada com o baralho de referência **em ordem conhecida** e
nenhuma run ativa, rode no Pi (ou em qualquer computador da rede, trocando `localhost`):

```bash
mkdir -p fotos_ref && for i in $(seq -w 1 100); do curl -s -X POST localhost:8000/calibration/feed > /dev/null; curl -s -o fotos_ref/$i.jpg "localhost:8000/calibration/frame.jpg?overlay=0&width=4096"; curl -s -X POST localhost:8000/calibration/drop > /dev/null; done
```

Cada carta é alimentada, fotografada em resolução cheia e cai no bin 6 (`seq -w 1 100` = 100 cartas).

### E2. Gabarito e benchmark

Crie `gabarito.csv` com a carta de cada foto (edição e número como impressos na carta):

```
file,set,number
001.jpg,FIN,306
002.jpg,FIC,61
```

O benchmark carrega um segundo OCR, pesado para o Pi 3B com o serviço no ar: rode no PC (copie `fotos_ref/`) ou
pare o serviço antes.

```bash
python -m cardforge.cv batch fotos_ref --truth gabarito.csv --out relatorios_ref
```

**Esperado:** o resumo mostra `Acurácia (RNF18)` (meta ≥ 90 %) e o tempo médio por captura. Em `relatorios_ref/`
há, por foto, a imagem anotada e o texto lido; `resultados.csv` reúne tudo.

### E3. Tempo por carta na máquina (RNF22)

Faça uma run completa com 50 cartas e veja `started_at` / `stopped_at` em `curl localhost:8000/runs`: a meta é
≥ 7 cartas/min sem retentativas (≤ 8,6 s por carta).

---

## Parte F — Uso normal

### F1. Serviço no boot

Ajuste `User`, `WorkingDirectory`, a porta serial e `MTG_APP_PASSWORD` em `deploy/sorter-api.service`, depois:

```bash
sudo cp deploy/sorter-api.service /etc/systemd/system/
```

```bash
sudo systemctl daemon-reload
```

```bash
sudo systemctl enable --now sorter-api
```

Logs ao vivo:

```bash
journalctl -u sorter-api -f
```

### F2. App

No `.env` do app: `USE_MOCK=false`, `API_BASE_URL=http://<ip-do-pi>:8000`. Login: usuário `admin`, senha de
`MTG_APP_PASSWORD`. Start/Resume, Stop e Cards in folders funcionam; uma run pausada por falha aparece como
*stopped* e Start/Resume a retoma.

### F3. Depois de cada run

Esvazie os bins (o Start recusa bins com cartas), e consulte os resultados em `/runs/<run_id>` ou exporte
`/inventory/export.csv`.

---

## Problemas comuns

| Sintoma | Causa provável / o que fazer |
|---|---|
| `MODULE_OFFLINE` (503) no Start | Arduino sem energia, cabo A/B invertido, baud diferente, porta serial errada (`MTG_SERIAL_PORT`) |
| `CARD_DB_MISSING` no Start | Falta `data/cards.db`: `.venv/bin/python -m cardforge.cv sync` |
| `TRAYS_NOT_EMPTY` sem cartas nos bins | IR dos bins invertido ou desalinhado (hoje o `a2_base` responde sempre "vazio": os IR dos bins ainda não estão ligados) |
| Run pausada com `FEED_TIMEOUT` | Carta presa no alimentador ou pilha vazia com o IR da pilha dizendo o contrário. Libere e use Resume |
| Run pausada com `DROP_FAILED` | Carta presa na plataforma: retire-a (ela não é registrada) e use Resume |
| Muitas cartas no bin 6 | Calibração (parte D): brilho, foco, enquadramento. Veja `attention` nos eventos e os motivos em `/runs/<id>` |
| Página de calibração: `VISION_OFF` | Serviço rodando com `MTG_VISION=off` |
| Página de calibração: `RUN_ACTIVE` | Há uma run ativa ou pausada: termine com Stop |
| Log: `expects sensor imx219 but the camera reports ...` | Câmera errada ou `dtoverlay` antigo no `config.txt` (parte B1) |
| `rpicam-hello` diz que a câmera está em uso | O serviço está com ela aberta: `sudo systemctl stop sorter-api` |
| LED de status e luz não mudam | O firmware do A1 ainda não tem `LED`/`LIGHT` (PROTOCOL.md 5.1); o log avisa uma vez |
