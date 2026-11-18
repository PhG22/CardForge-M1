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
| **Banco de cartas** | [`cardforge/data/`](../cardforge/data/), `data/cards.db` | espelho local do Scryfall com os nomes em inglês **e em português**, criado por `python -m cardforge.cv sync` |
| **Configuração da visão e da câmera** | [`vision.toml`](../vision.toml) | limiares, câmera (IMX219), luz; a calibração grava aqui |
| **Firmware** | [`firmware/`](../firmware/) | `a1_top` (módulo de cima: rolo, flap, IR da pilha e da plataforma), `a2_base` (base: motor, homing); `a2_homing_test` e `a2_tray_move_test` são testes de bancada só da base |
| **Protocolo RS-485** | [docs/PROTOCOL.md](PROTOCOL.md) | quadros, comandos, códigos de erro; 5.1 = comando `LIGHT` que falta no firmware |
| **LED de status** | [`sorter_api/status_led.py`](../sorter_api/status_led.py) | LED RGB no GPIO do Pi (17, 22, 27): cores e piscadas do Apêndice B, estado da rede |
| **Recorte fixo da carta** | [`cardforge/cv/detection/fixed.py`](../cardforge/cv/detection/fixed.py), `vision.toml` `[crop]` | os 4 cantos onde a carta para; calibrados pela página `/calibration` |
| **Wi-Fi por Bluetooth** | [`pi_ble_setup/`](../pi_ble_setup/) | serviço separado (README próprio) |
| **App mobile (no repositório)** | [`cardforgeapp/`](../cardforgeapp/) | projeto Flutter |
| **Referência da API** | [docs/API.md](API.md) | cada rota, resposta e evento |
| **Serviço no boot** | [`deploy/sorter-api.service`](../deploy/sorter-api.service) | unidade systemd |
| **Testes** | [`tests/`](../tests/) | `test_vision` (visão), `test_protocol` (quadros), `test_api` (API + simulador), `test_integration` (loop completo, persistência, app, visão real em fotos), `test_calibration` |
| **App mobile** | pasta `CardForge App` (projeto Flutter separado) | sem alterações; aponta para o Pi pelo `.env` |
| **Prova de conceito antiga** | [`deprecated/`](../deprecated/) | só referência; pode ser apagada |
| **Fotos da calibração do recorte** | `calibragem_recorte/` (criada na primeira medição) | cada foto usada para medir o recorte fixo: original, anotada, `medicoes.csv` e o recorte salvo |
| **Arquivos de sistema do Pi** | [`sorter_api/housekeeping.py`](../sorter_api/housekeeping.py) | guarda as fotos da calibração e limpa os temporários ao fim de cada run |
| **Benchmark de cada run** | [`sorter_api/benchmark.py`](../sorter_api/benchmark.py), `benchmarks/` (criada na 1.ª run) | média, mediana, moda, mínimo e máximo do tempo por carta, geral e first-try (parte E4) |
| **Cartas lentas** | [`sorter_api/slow_cards.py`](../sorter_api/slow_cards.py), `cards_to_improve/` (criada na 1.ª carta lenta) | foto original, recorte anotado, resultado e tempos de cada carta que levou mais de 3 s para ser identificada (parte E5) |

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

O `data/cards.db` já vem no repositório, com os nomes em inglês e em português. Para atualizá-lo (precisa de
internet; baixa ~80 MB do `default_cards` e lê ~400 MB do `all_cards` em streaming, sem gravar no disco):

```bash
python -m cardforge.cv sync
```

`--langs ""` faz o banco só com os nomes em inglês (download de ~80 MB); `--langs pt,es` acrescenta outro idioma.

### A2. Rodar os testes

```bash
python -m pytest -q
```

**Esperado:** `139 passed` e `36 skipped`. Os testes usam os Arduinos simulados; `test_integration` e
`test_calibration` também rodam a visão de verdade sobre fotos de `input_cartas/`. Os pulados são os do servidor e
do banco (PostgreSQL); para rodá-los veja **A8**.

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

**Esperado:** evento `fault` com `FEED_TIMEOUT` depois das **2 tentativas de puxar** (RF05):
`curl localhost:8000/hardware/status` mostra `"feed_attempt": 2` no `top`, e `curl localhost:8000/run/status`
mostra `"state": "paused"` e a mensagem "after 2 pull attempts". Com o servidor ligado (A8), o mesmo alerta aparece
em `GET /notifications` do usuário (RF38). Destrave e retome:

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
separadas por bin. (Assim, sem servidor, não há cadastro, histórico nem exportação: veja A8.)

### A8. Servidor CardForge + PostgreSQL (contas, histórico, coleção, CSV)

1. **PostgreSQL** (uma vez). Com Docker: `docker compose -f deploy/docker-compose.postgres.yml up -d`. Sem Docker
   (Linux): `sudo -u postgres psql -c "CREATE ROLE cardforge LOGIN PASSWORD 'cardforge' CREATEDB;"` e
   `sudo -u postgres createdb -O cardforge cardforge`.
2. **Dependências e testes do servidor**:
   ```bash
   pip install -r cardforge_server/requirements.txt
   CF_TEST_DATABASE_URL=postgresql://cardforge:cardforge@localhost:5432/postgres python -m pytest -q
   ```
   **Esperado:** `175 passed` (o teste `test_end_to_end_run_is_saved_to_the_users_account` faz o caminho inteiro:
   run simulada → `run.db` → envio → PostgreSQL → consultas do app; `test_end_to_end_a_jam_reaches_the_apps_notification_feed`
   faz o do alerta: atolamento → Pi → servidor → a espera do app recebe o alerta, RF38).
3. **Servidor** (aplica as migrações sozinho; documentação em `http://localhost:8080/docs`):
   ```bash
   export CF_DATABASE_URL=postgresql://cardforge:cardforge@localhost:5432/cardforge
   export CF_TOKEN_SECRET=troque-isto-por-um-segredo-longo CF_MACHINE_TOKENS=dev-token
   python -m cardforge_server
   ```
4. **Pi simulado ligado ao servidor**, em outro terminal:
   ```bash
   MTG_SERVER_URL=http://localhost:8080 MTG_MACHINE_TOKEN=dev-token MTG_VISION=files python -m sorter_api --simulate
   ```
5. **App**: no `.env`, `USE_MOCK=false`, `USE_MOCK_MACHINE=false`, `API_BASE_URL=http://<ip-do-pc>:8080` (o
   servidor, não o Pi) e rode `flutter pub get` antes do `flutter run`. Em **Create account** crie um usuário, vincule
   a máquina, faça uma run; ao terminar ela aparece em **Sorting runs** (toque para ver o resultado), as cartas em
   **Cards in folders**, e **CSV report** exporta a coleção.
6. **Queda do servidor**: pare o servidor no meio de uma run. A run termina normalmente; `GET
   http://localhost:8000/sync/status` mostra `"failed": 1`. Suba o servidor de novo: em até alguns segundos (ou na
   hora, com `POST /sync/now`) fica `"synced"` e a run aparece no app, sem cartas duplicadas.

Sem `MTG_SERVER_URL`, o Start do Pi não pede login (como antes) e as runs ficam só no `run.db`.

---

## Parte B — Preparar o Raspberry Pi

### B1. Sistema

1. Raspberry Pi OS **64-bit** (Bookworm ou mais novo). O onnxruntime não existe para 32 bits.
2. Câmera: IMX219 (Camera Module v2) ou OV5647 (Camera Module v1) — o serviço detecta qual está ligada e usa o
   perfil dela. Em `/boot/firmware/config.txt` deixe `camera_auto_detect=1` e nenhum `dtoverlay=imx219` /
   `dtoverlay=ov5647` fixo, para poder trocar de módulo sem editar o arquivo. Reinicie.
3. Confira a câmera (com o serviço ainda não instalado):
   ```bash
   rpicam-hello --list-cameras
   ```
   **Esperado:** uma linha com `imx219` ou `ov5647`.
4. Barramento: com o conversor **USB-RS485**, confira que ele aparece:
   ```bash
   ls /dev/ttyUSB*
   ```
   Com um MAX485 na UART do GPIO: em `sudo raspi-config` → *Interface Options* → *Serial Port*: login shell **não**,
   hardware serial **sim**.

### B2. Código

```bash
git clone <url-do-repositório> /home/raspad/CardForge-M1
```

```bash
cd /home/raspad/CardForge-M1
```

```bash
sudo apt install -y python3-picamera2 python3-gpiozero python3-lgpio
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

O último comando cria/atualiza o `data/cards.db` (precisa de internet só agora; depois tudo funciona offline). Ele
baixa ~480 MB da Scryfall (o bulk de todos os idiomas, para os nomes em português) e leva alguns minutos no Pi 3B;
o banco do repositório já vem pronto, então rode-o só para atualizar as cartas — e nem isso é preciso no dia a
dia: **o serviço confere a Scryfall toda vez que sobe** e o app avisa quando há cartas novas; a atualização é
iniciada pelo app (parte C1c). Alternativa: rode o `sync` no PC e
copie o resultado com `scp data/cards.db raspad@<ip-do-pi>:/home/raspad/CardForge-M1/data/` (pare o serviço antes).

```bash
.venv/bin/python -m pytest -q
```

**Esperado:** `101 passed` também no Pi (mais lento).

### B3. Firmware (Arduino IDE, no PC)

1. `firmware/a1_top/a1_top.ino` → Arduino do **módulo de cima** (endereço A1). Pinos padrão: rolo D9, flap D10,
   IR da plataforma D6, IR da pilha D5, motor de vibração da plataforma D7 (via BC548),
   RS-485 em D4 (RO) / D3 (DI) / D13 (DE+RE).
2. `firmware/a2_base/a2_base.ino` → Arduino da **base** (endereço A2). Pinos: STEP D5, DIR D6, SLEEP D4, RESET D3,
   M2 D2, IR do homing A5, RS-485 em A0 (RO) / A1 (DI) / D13 (DE+RE), IR dos bins: bin 1 D10, bin 2 D8, bin 3 D11,
   bin 4 D7, bin 5 D9, bin 6 D12 (FC-51, LOW = carta no bin).
3. Os dois a 9600 baud (igual a `MTG_BAUDRATE`). Os pinos estão no começo de cada `.ino`; mude lá se a fiação
   for diferente.
4. **Versão 1.1** dos dois (`PING` responde `FW=1.1`): o A1 faz as 2 tentativas de puxar do RF05 e mostra
   `ATTEMPT=` no STATUS; o A2 usa o perfil rápido (`FAST_PROFILE = true`) e o homing rápido com a posição guardada
   (RF08). Grave os dois de novo depois de atualizar o repositório.

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

**Sensores IR dos bins:** com todos os bins vazios, `"trays_with_cards": []`. Ponha uma carta em cada bin, um de cada
vez, e confira que só aquele número aparece (ex.: carta no bin 4 → `[4]`). Número errado = fio trocado (corrija em
`TRAY_IR_PINS` no `a2_base.ino`); bin que nunca aparece = sensor desalinhado ou desligado (com o pull-up interno, um
sensor solto lê "vazio"). Com uma carta em algum bin, `curl -X POST <ip-do-pi>:8000/run/start` deve responder 409
`TRAYS_NOT_EMPTY` listando o bin.

### C1c. Base de cartas: avisos e atualização pelo app

Ao subir, o serviço só **confere** na Scryfall se há cartas novas (em segundo plano; a máquina fica pronta como
sempre). Se há, o app avisa: um alerta ("Atualização da base de cartas disponível"), uma linha no card da máquina
na Home e o painel **Base de cartas** no Controle da máquina, que diz o tamanho do download. Para testar:

1. Faça a máquina achar que há cartas novas e reinicie o serviço:
   `sqlite3 data/cards.db "update db_metadata set value='x' where key='sets_fingerprint'"` e
   `sudo systemctl restart sorter-api`. Em alguns segundos o app avisa (`curl localhost:8000/card-db` →
   `update_available: true`).
2. No app, **Atualizar agora** e confirme. A barra mostra a etapa (nomes em português, lista de cartas, montagem
   do banco), a porcentagem, os MB baixados e o tempo restante; o LED pisca em branco e um Start responde
   409 `CARD_DB_UPDATING`. **Cancelar atualização** para tudo e mantém o banco atual.
3. No fim, o app avisa "Base de cartas atualizada" e o LED volta ao verde. **Procurar atualizações** confere de
   novo quando quiser.

Durante uma run a atualização é recusada (409 `RUN_ACTIVE`). Sem internet ou com a Scryfall fora do ar, a máquina
segue com o banco local. Log: `journalctl -u sorter-api | grep "card database"`.

### C1b. LED de status

Ao subir o serviço o LED fica **branco fixo** (iniciando: carregando a visão e fazendo o homing da base) e depois
**verde fixo** (pronto), **azul fixo** (nenhum Wi-Fi configurado: pareie pelo app) ou **azul piscando** (Wi-Fi
configurado, fora de alcance). Durante uma atualização da base de cartas (parte C1c) ele pisca em **branco**.
Confira os outros estados: Start com um bin ocupado → **amarelo fixo**;
uma run → **verde piscando**; trave o alimentador durante uma run → **vermelho fixo** (Resume volta ao verde
piscando). Desconecte o Wi-Fi com a máquina parada (`sudo nmcli radio wifi off`) → **azul piscando** em até 2 s
(`sudo nmcli radio wifi on` para voltar). LED apagado ou cor trocada: confira a fiação (COM no pino 9, vermelho 11,
azul 13, verde 15) ou ajuste `MTG_LED_PINS` (ordem R,G,B).

### C2. Só o módulo de cima (se a base ainda não estiver pronta)

```bash
MTG_SERIAL_PORT=/dev/ttyUSB0 MTG_VISION=off .venv/bin/python -m sorter_api --top-only
```

```bash
curl -X POST <ip-do-pi>:8000/run/start
```

**Esperado:** cada carta é alimentada até a plataforma, espera 2 s e cai; ao esvaziar a pilha, a run termina.

### C2b. Flap

```bash
curl -X POST <ip-do-pi>:8000/flap -H 'Content-Type: application/json' -d '{"angle": 5}'
```

Mova o flap para qualquer ângulo (0–180) até achar a melhor abertura; `{"position": "open"}` / `{"position": "closed"}`
usa os ângulos da run (aberto 5°, fechado 80°). Para a run usar outro ângulo sem reiniciar a API:
`curl -X PUT <ip-do-pi>:8000/flap/angles -H 'Content-Type: application/json' -d '{"open_angle": 8}'`. Isso não fica
salvo: para manter, use `MTG_FLAP_OPEN_ANGLE` / `MTG_FLAP_CLOSED_ANGLE`. Cuidado: abrir o flap derruba a carta que
estiver na plataforma.

### C3. Base

```bash
curl -X POST <ip-do-pi>:8000/base/home
```

```bash
curl -X POST <ip-do-pi>:8000/base/goto/1
```

Teste os bins de 1 a 6 e depois `base/home` de novo: `drift` entre 0 e ±5 = sem perda de passos. Com o perfil
rápido (`FAST_PROFILE = true`, padrão no `a2_base.ino` 1.1) repita isso umas 10 vezes; se o `drift` passar de ±5,
volte a `FAST_PROFILE = false` (mais lento: o RNF22 e o RF08 ficam em risco) ou diminua `ACCEL`.

Tempo do homing (RF08, meta ≤ 5,0 s): `journalctl -u sorter-api | grep "base homed"` mostra cada um ("at power-on
in N s", "before the run in N s"). Com a base estacionada no bin 3 depois de uma run, o homing fica perto de 1 s.

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
ssh -L 8000:localhost:8000 raspad@<ip-do-pi>
```

1. **Recorte fixo** (um por câmera): ponha cartas comuns na pilha; **Alimentar 1 carta** → **Medir posição desta
   carta** → **Descartar carta (bin 6)**, repetindo com 4 ou 5 cartas; depois **Salvar recorte fixo**. Medições
   discrepantes ficam fora da média (a página lista as descartadas). A *variação entre as cartas* deve ficar em até
   ~30 px; se passar disso, a carta não para sempre no mesmo lugar (confira o flap) ou use `[crop] margin`.
   **Se a borda detectada (verde) não for a borda externa da carta** — por exemplo cortando o rodapé com edição e
   número, como acontece quando a borda preta da carta encosta no suporte preto —, use **Marcar os cantos à mão**:
   **Carregar foto**, toque nos 4 cantos da borda externa (1 = início do título, ..., 4 = lado da edição),
   **Pré-visualizar recorte** (a carta deve aparecer em pé e inteira; senão **Girar 90°** ou ajuste os pontos) e
   **Salvar estes cantos**. A pré-visualização também mostra a nitidez do rodapé, que deve passar de 70.
   Sem esse passo, o Start é recusado com `CROP_NOT_CALIBRATED`.
   Cada medição guarda a foto em `calibragem_recorte/` na pasta do projeto: a original em resolução cheia,
   uma cópia com a borda detectada desenhada (`_anotada`), e uma linha em `medicoes.csv` com os cantos. Fotos em
   que a borda não foi encontrada também ficam (`_sem_borda`), e cada **Salvar recorte fixo** gera um
   `recorte_salvo_<data>.json`. Para trazer as fotos para o PC e mandá-las para análise:
   `scp -r raspad@<ip-do-pi>:/home/raspad/CardForge-M1/calibragem_recorte .`
2. **Cartão branco** liso do tamanho de uma carta no topo da pilha → **Alimentar 1 carta**.
   **Autocalibrar (exposição + branco)**. Em **Medidas**: *luz desigual* ≤ 20 % e *pixels estourados* ≤ 2 %.
   Fora disso, ajuste a posição/difusão das fitas LED (essa é a única parte física).
3. **Descartar carta (bin 6)**, ponha cartas comuns na pilha e **Alimentar 1 carta**. **Capturar** (ou **Ao vivo**):
   *brilho da carta* entre 60 e 180, *nitidez do texto pequeno* acima de 70, *desvio × recorte fixo* até 2 %.
   Ajuste exposição, ganho e luz e **Aplicar**.
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

O benchmark carrega um segundo OCR, pesado para o Pi 3B com o serviço no ar: rode no PC (copie `fotos_ref/` e o
`vision.toml` calibrado) ou pare o serviço antes. As fotos da máquina usam o recorte fixo do `vision.toml`; para
fotos de outras fontes (como `input_cartas/`), acrescente `--detect` ao comando.

```bash
python -m cardforge.cv batch fotos_ref --truth gabarito.csv --out relatorios_ref
```

**Esperado:** o resumo mostra `Acurácia (RNF18)` (meta ≥ 90 %) e o tempo médio por captura. Em `relatorios_ref/`
há, por foto, a imagem anotada e o texto lido; `resultados.csv` reúne tudo.

### E3. Tempo por carta na máquina (RNF22)

Faça uma run completa com 50 cartas. No fim, `curl localhost:8000/run/status` traz `timing`:
`nominal_cards_per_min` (só as cartas sem retentativa, o que o RNF22 mede; meta ≥ 7, ou seja ≤ 8,6 s por carta),
`cards_per_min` (todas) e `slowest_capture_s` (RF02/RF15: ≤ 2,0 s). A mensagem do fim da run também diz as cartas
por minuto. Para ver onde vai o tempo: `journalctl -u sorter-api | grep "card [0-9]*:"` lista, por carta,
`feed_s`, `capture_s`, `identify_s`, `move_s`, `drop_s` e `total_s`. Os tempos ajustáveis do lado do Pi são
`MTG_FLAP_SETTLE_S` (0,5) e `MTG_CARD_FALL_S` (0,5).

### E4. Benchmark de cada run (geral e first-try)

Ao fim de **toda** run (pilha vazia, Stop, limite de 250 ou falha) o serviço calcula, para dois tempos por carta:

* `identify_s` — a identificação: da primeira foto à decisão, com as novas capturas (RF29) incluídas;
* `total_s` — o ciclo inteiro da carta: do início da alimentação ao fim da queda (RNF22);

a **média, mediana, moda, mínimo e máximo** em dois grupos:

* `general` — todas as cartas processadas na run;
* `first_try` — só as lidas de primeira, sem nenhum failsafe: identificadas (ou dadas como viradas) na 1.ª captura,
  com o mínimo de OCR (sem reler o título a 180°, sem o OCR de layout, sem a checagem de verso depois de uma
  identificação falhada) e alimentadas na 1.ª puxada (RF05).

A moda é o tempo mais comum arredondado a 0,1 s (empate: o menor; `mode_count` = quantas cartas têm esse tempo).
Cada carta que não foi first-try diz por quê em `fallbacks`: `retry`, `orientation_reread`, `layout_ocr`,
`face_down_after_identify`, `not_identified`, `vision_error`, `feed_retry`.

Onde fica:

| Onde | O quê |
|---|---|
| `benchmarks/benchmark_runs.csv` | uma linha por run × grupo × tempo — todas as runs no mesmo arquivo, fácil de comparar no Excel |
| `benchmarks/<data>-<hora>_<run8>.json` | o mesmo, mais quantas cartas foram first-try, a contagem de cada `fallback` e os tempos de cada carta |
| `run.db`, tabela `run_benchmark` | o mesmo, por run; aparece em `GET /runs/<id>` (`benchmark`) |
| `run.db`, `local_cards` | `identify_s`, `total_s`, `first_try` e `fallbacks` de cada carta (também em `GET /runs/<id>`) |
| `GET /run/status` → `timing` | `first_try_cards` e o `benchmark` da run atual (ou da última), ao vivo |

O log do fim da run resume: `run ... benchmark: general n=50 identify mean 2.31 s median 1.98 s ...`. Para trazer
para o PC: `scp -r raspad@<ip-do-pi>:/home/raspad/CardForge-M1/benchmarks .`

### E5. `cards_to_improve/`: as cartas que demoram

Toda carta cuja identificação passa de **3 s** (`MTG_SLOW_CARD_S`) é guardada para estudo:

```
cards_to_improve/20261117-143212_3d5b829a/      uma pasta por run (só se teve carta lenta)
  card_007/
    captura_1_original.jpg                      foto da câmera de cada captura, em resolução cheia
    captura_1_recorte_anotado.jpg               a carta recortada com as ROIs lidas, status e confiança
    captura_2_original.jpg ...                  (uma por captura, RF29)
    resultado.json                              tempos (alimentação, cada captura: luz, foto, reconhecimento,
                                                chamadas de OCR; base, queda, total), o que cada captura leu
                                                e com que carta bateu, o destino final, first-try e o porquê
  indice.csv                                    uma linha por carta lenta (ordene por identify_s no Excel)
  resumo.json                                   a run, o limite usado, quantas cartas, MB gastos, as 5 mais lentas
```

As fotos são gravadas por uma thread de prioridade mínima enquanto a base gira e a carta cai (a CPU está livre
nessa hora); o índice e o resumo, no fim da run. A pasta nunca é apagada pela limpeza do fim da run: apague as runs
antigas à mão depois de estudá-las. Passando de `MTG_SLOW_CARDS_MAX_MB` (1000 MB), as cartas lentas seguintes
ficam só com o `resultado.json`. `MTG_SLOW_CARD_S=0` desliga. Para trazer para o PC:
`scp -r raspad@<ip-do-pi>:/home/raspad/CardForge-M1/cards_to_improve .`

Para estudar uma carta no PC, rode a visão de novo na foto original (usa o recorte fixo do `vision.toml`):
`python -m cardforge.cv identify cards_to_improve/<run>/card_007/captura_1_original.jpg`. O `debug` do resultado
mostra o que pesou: `ocr_calls`, `score_rotated` (releu o título a 180°), `layout_ocr_skipped`, `title_score`.

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

LED branco desde o primeiro segundo do boot (antes do serviço subir): acrescente `gpio=17,22,27=op,dh` ao
`/boot/firmware/config.txt` e reinicie (`sudo reboot`). Ligue o Pi e confira: branco → verde / azul.

Logs ao vivo:

```bash
journalctl -u sorter-api -f
```

### F2. App

Com o servidor CardForge (recomendado, [SERVER.md](SERVER.md)): no Pi, `MTG_SERVER_URL` e `MTG_MACHINE_TOKEN`
no serviço; no `.env` do app, `USE_MOCK=false` e `API_BASE_URL=http://<ip-do-servidor>:8080`. Cada usuário cria a
sua conta no app; a run fica na conta de quem apertou Start.

Notificações (RF38): na primeira run o app pede permissão de notificações e para ignorar a otimização de bateria;
aceite as duas. Teste com o app em segundo plano e a tela bloqueada: trave o alimentador (tire as cartas da
frente do rolo) → chega "Card jam detected" com o horário; Resume e deixe terminar → chega "Sorting finished".

Sem servidor: `API_BASE_URL=http://<ip-do-pi>:8000`, usuário `admin`, senha de `MTG_APP_PASSWORD`. Start/Resume,
Stop e Cards in folders funcionam; uma run pausada por falha aparece como *stopped* e Start/Resume a retoma.

### F3. Depois de cada run

Esvazie os bins (o Start recusa bins com cartas). Os resultados ficam no app (**Sorting runs**, **CSV report**);
no Pi, `GET /sync/status` diz se a run já foi enviada ao servidor, e `/runs/<run_id>` e `/inventory/export.csv`
continuam como visão local da máquina.

---

## Problemas comuns

| Sintoma | Causa provável / o que fazer |
|---|---|
| `MODULE_OFFLINE` (503) no Start | Arduino sem energia, cabo A/B invertido, baud diferente, porta serial errada (`MTG_SERIAL_PORT`) |
| `CARD_DB_MISSING` no Start | Falta `data/cards.db`: `git pull` (o repositório traz o banco) ou `.venv/bin/python -m cardforge.cv sync`; com internet, reiniciar o serviço também o cria |
| `CARD_DB_UPDATING` no Start / LED branco piscando | Uma atualização da base de cartas iniciada no app está em andamento. Acompanhe ou cancele no Controle da máquina (`curl localhost:8000/card-db` → `progress`) |
| O app nunca avisa de atualização | `curl localhost:8000/card-db` → `last_check.result`: `offline` (Wi-Fi não conectou em 30 s no boot: aumente `MTG_CARD_DB_CHECK_WAIT_S`), `unreachable` (sem internet / Scryfall fora do ar) ou `failed` (veja o log). **Procurar atualizações** no app confere de novo. `MTG_CARD_DB_CHECK=off` desliga a checagem ao ligar |
| `CROP_NOT_CALIBRATED` no Start | Salve o recorte fixo na página `/calibration` (parte D, passo 1) |
| `TRAYS_NOT_EMPTY` sem cartas nos bins | Sensor IR do bin desalinhado (vendo a parede) ou fio trocado: confira bin a bin (parte C1) |
| Muitas cartas `CROP_FAILED` ou lidas tortas | Câmera ou plataforma mudaram de lugar: refaça o recorte fixo (parte D, passo 1). As fotos usadas estão em `calibragem_recorte/` |
| Sumiram arquivos de `capturas/` ou `relatorios/` | Ao fim de cada run o serviço esvazia as pastas temporárias (`MTG_CLEANUP_DIRS`); copie o que quiser guardar antes, ou use outro nome de pasta (ex.: `relatorios_ref`) |
| Cartas em português lentas ou no bin 6 | `cards.db` sem os nomes em português: `sqlite3 data/cards.db "select * from db_metadata"` deve mostrar `printed_langs = pt` e `schema_version = 2`. Senão, `git pull` ou `.venv/bin/python -m cardforge.cv sync`, e reinicie o serviço |
| Cartão SD enchendo | `cards_to_improve/` guarda fotos de resolução cheia das cartas lentas: apague as runs já estudadas, baixe `MTG_SLOW_CARDS_MAX_MB` ou desligue com `MTG_SLOW_CARD_S=0` |
| Run pausada com `FEED_TIMEOUT` | Carta presa no alimentador ou pilha vazia com o IR da pilha dizendo o contrário. Libere e use Resume |
| Run pausada com `DROP_FAILED` | Carta presa na plataforma mesmo depois de 3 pulsos do motor de vibração: retire-a (ela não é registrada) e use Resume. Se acontece sempre, confira o motor (D7, `VIB=` no STATUS) e se o `a1_top.ino` 1.2 foi gravado |
| Muitas cartas no bin 6 | Calibração (parte D): brilho, foco, enquadramento. Veja `attention` nos eventos e os motivos em `/runs/<id>` |
| Página de calibração: `VISION_OFF` | Serviço rodando com `MTG_VISION=off` |
| Página de calibração: `RUN_ACTIVE` | Há uma run ativa ou pausada: termine com Stop |
| Log: `expects sensor imx219 but the camera reports ...` | Câmera errada ou `dtoverlay` antigo no `config.txt` (parte B1) |
| `rpicam-hello` diz que a câmera está em uso | O serviço está com ela aberta: `sudo systemctl stop sorter-api` |
| Luz da câmara não muda | O firmware do A1 ainda não tem `LIGHT` (PROTOCOL.md 5.1); o log avisa uma vez |
| LED de status apagado | Fiação (COM no pino 9) ou `MTG_LED=off`; no log, `status LED disabled` indica que o GPIO não abriu |
