# LYSec Play | Live Viewer

Visualizador de RTSP de câmeras Yoosee em **HLS** reproduzível no navegador,
integrado ao **Central Cameras** e com detecção opcional de objetos/pessoas.

- Servidor, supervisor e API usam **somente biblioteca padrão**; `numpy` e
  `opencv-python-headless` são necessários apenas para a IA.
- **FFmpeg** faz a conversão: H.265/HEVC → H.264, G.711 → AAC.
- **Um processo FFmpeg por câmera**, supervisionado e reiniciado automaticamente.
- Página responsiva com player, estado da conexão e botão de reinício.
- **Replay de até 30 minutos**: arraste a barra abaixo do vídeo para voltar no tempo.
- **Visão computacional**: YOLOv9-t + OC-SORT desenham caixas **só de pessoas**
  e geram eventos com snapshot, sem abrir uma segunda conexão RTSP.
- **Recortes laterais**: rostos aparecem na faixa esquerda do vídeo e placas de
  veículos na direita, em fila; quando a faixa enche, os mais antigos saem.
- Credenciais fora dos arquivos públicos, logs com senha redigida.
- HLS com retenção limitada: segmentos antigos são apagados automaticamente.

---

## 1. Requisitos

| Dependência | Por quê | Como verificar |
|---|---|---|
| Python 3.10+ | servidor HTTP, supervisor e API (stdlib) | `python3 --version` |
| FFmpeg com `libx264` e `aac` | decodificar HEVC e gerar HLS H.264/AAC | `ffmpeg -hide_banner -encoders \| grep -E "libx264|aac "` |
| Navegador com MSE (Chrome/Edge/Firefox) ou HLS nativo (Safari) | reproduzir HLS | — |
| `numpy` + `opencv-python-headless` | **opcional**: detecção YOLOv9 via OpenCV DNN | `python3 -c "import cv2, numpy; print(cv2.__version__)"` |

O `hls.js` 1.5.17 já está vendorizado em `static/hls.min.js`; **não** é baixado em tempo
de execução e não há dependência de CDN.

O viewer não instala nada globalmente. Para habilitar a detecção, instale as
dependências locais com `pip install -r live_viewer/requirements.txt` (ou as do
`requirements.txt` na raiz do Central). Se o FFmpeg não estiver presente:

```bash
sudo apt install ffmpeg        # Ubuntu/Debian — requer sua autorização
```

---

## 2. Estrutura

```
live_viewer/
├── app.py               # servidor HTTP, rotas, página, sinais, lock de instância
├── camera_manager.py    # config/segredos + supervisor (1 thread por câmera)
├── camera_store.py      # valida e grava câmeras adicionadas pela interface
├── ffmpeg_manager.py    # comando FFmpeg, processo, PID file, limpeza de órfãos
├── logging_utils.py     # log rotativo com redação de credenciais
├── config.json          # câmeras, HLS e detecção (sem senha)
├── secrets.json         # senhas (modo 600, fora do que é servido)
├── vision/              # YOLOv9 ONNX, rótulos COCO e OC-SORT
├── templates/index.html       # página com {{CAMERA_CARDS}}
├── templates/camera_card.html # markup de cada cartão (botões + logs)
├── static/player.js     # player hls.js, polling de status e overlay de detecções
├── static/cameras.js    # formulários adicionar/renomear/remover câmera
├── static/style.css
├── static/hls.min.js    # biblioteca vendorizada
├── streams/<id>/        # playlist .m3u8 + segmentos .ts + .ffmpeg.pid
├── models/              # yolov9t.onnx, baixado em runtime (gerado)
├── events/<id>/         # events.jsonl + snapshots anotados (gerado)
├── logs/                # viewer.log + ffmpeg-<id>.log (rotativos)
├── requirements.txt     # numpy + opencv-python-headless (só para a IA)
└── start.sh
```

`secrets.json`, `streams/*`, `logs/*`, `models/*` e `events/*` estão no `.gitignore`.

---

## 3. Configuração

### Adicionando câmeras pela interface (sem editar arquivos)

Abra o visualizador (ou a aba **Ao vivo** no Central Cameras) e clique em
**+ Adicionar câmera**. Preencha:

- **Nome da câmera**: como ela aparece na tela (ex.: “Portão”).
- **IP ou endereço** e **porta RTSP** (padrão `554`).
- **Usuário** e **senha** (padrão de usuário: `admin`).
- **Caminho do stream**: `/onvif1` nas Yoosee testadas; `/onvif2` é a câmera
  de baixa resolução.
- **Transporte**: UDP (padrão). Use TCP somente se o firmware exigir.
- Em **Ajustes avançados**, largura, FPS e bitrates — deixe os padrões se a
  câmera nova for igual às outras.

Ao confirmar, a câmera é salva e começa a tentar conexão na hora, sem
reiniciar o visualizador. A senha fica somente em `secrets.json` (permissão
`600`) e nunca aparece na página nem em `config.json`. É possível adicionar
uma de cada vez e testar antes de adicionar a próxima.

### Removendo câmeras pela interface

No cartão da câmera, clique em **Remover** e confirme. O viewer:

1. apaga a câmera de `config.json` e a senha de `secrets.json` (somente se a
   mesma chave de segredo não estiver sendo usada por outra câmera);
2. encerra o supervisor/FFmpeg e a pasta `streams/<id>/` daquela câmera;
3. encerra o worker de detecção e apaga `events/<id>/`.

As outras câmeras seguem normais. A mesma operação está disponível via
`POST /api/cameras/<id>/remove` (sem corpo). Se a câmera estiver temporariamente
fora do ar, a remoção ainda funciona: a configuração é gravada antes da limpeza.

### Câmera pública de teste (Copacabana)

Já deixei cadastrada uma câmera pública brasileira em `config.json`:

- **Id**: `pub-copacabana`
- **Nome**: `Copacabana (pública)`
- **Fonte**: `https://www.youtube.com/watch?v=A9Ztonz1Lgs` (Copacabana Posto 6,
  Rio de Janeiro; a mesma câmera do link
  `https://worldwatcher.live/cam/AYcY3pIV`)
- **Link de referência**: <https://worldwatcher.live/cam/AYcY3pIV>

Ela entra na lista como qualquer outra câmera: abre no player, recebe overlay de
detecção e pode ser removida pelo botão **Remover**. O campo `source_url` faz o
FFmpeg ler a transmissão pública em vez de montar um RTSP. Para YouTube, o
`yt-dlp` resolve a URL HLS assinada; como essas URLs expiram, ela é resolvida de
novo a cada reinício do stream (cache de 10 minutos). A fonte pública é
**somente vídeo** (sem áudio) e exige internet no computador que roda o viewer.

Para testar outra câmera pública, use **Adicionar câmera** e escolha
**Câmera pública (YouTube ou link HLS)**: basta informar um nome e o link da
transmissão ao vivo. Os campos RTSP são preenchidos automaticamente
(`youtube.com:443`, usuário/senha `publico`, caminho `/`, TCP) só para manter o
cadastro compatível com `config.json`; o acesso real usa apenas o `source_url`.
Também dá para converter uma câmera existente clicando na **engrenagem** no topo
direito do cartão (ao lado do badge de IA): o diálogo mostra todos os dados
usados para acessá-la — nome, host, porta, usuário, senha, caminho RTSP,
transporte, `source_url`, bitrates, FPS, ativação e IA — e todos podem ser
editados, exceto o `id` e a chave interna do segredo. Trocar o `source_url` por
outro link `https://www.youtube.com/watch?v=...` (de uma transmissão ao vivo)
já aplica a mudança e reinicia o stream. Salvar também reconfigura a detecção daquela
câmera. `yt-dlp` é dependência opcional, usada apenas quando existe `source_url`;
sem ela, a câmera pública fica com erro, mas as câmeras RTSP e a IA das demais
continuam normais.

### `config.json`

```json
{
  "server": { "host": "127.0.0.1", "port": 8088 },
  "hls": {
    "segment_seconds": 2,
    "playlist_size": 6,
    "delete_threshold": 2,
    "stall_timeout_seconds": 15,
    "start_grace_seconds": 12,
    "replay_minutes": 30
  },
  "detection": {
    "enabled": true,
    "model": "yolov9t.onnx",
    "model_url": "https://huggingface.co/Kalray/yolov9/resolve/main/yolov9t.onnx",
    "input_size": 640,
    "confidence": 0.25,
    "iou": 0.45,
    "fps": 2.0,
    "max_width": 960,
    "classes": ["person"],
    "max_age": 30,
    "min_track_len": 1,
    "event_cooldown_seconds": 60,
    "snapshot": true,
    "prefer_cuda": false,
    "face_model": "face_detection_yunet_2023mar.onnx",
    "face_model_url": "https://github.com/opencv/opencv_zoo/raw/main/models/face_detection_yunet/face_detection_yunet_2023mar.onnx",
    "face_confidence": 0.65,
    "crop_queue_size": 6,
    "crop_attempts": 8
  },
  "cameras": [
    {
      "id": "yoosee-190",
      "name": "Yoosee 10.0.0.190",
      "host": "10.0.0.190",
      "port": 554,
      "username": "admin",
      "secret": "camera_190_password",
      "path": "/onvif1",
      "transport": "udp",
      "enabled": true,
      "output_width": 1280,
      "output_height": 0,
      "video_bitrate": "2500k",
      "audio_bitrate": "64k",
      "input_fps": 15
    }
  ]
}
```

- `secret` é o **nome da chave** em `secrets.json`, não a senha.
- `output_height: 0` usa `output_width` e calcula a altura proporcional.
- `path`: `/onvif1` (1080p) ou `/onvif2` (640×360) nesta câmera.
- `transport`: use `udp` neste firmware. TCP falhou nos testes diretos de FFmpeg.
- `replay_minutes`: janela de replay desejada. **Máximo 30**; valores maiores são
  limitados a 30 e `0` desativa o replay. `playlist_size` continua sendo o mínimo de
  entradas ao vivo, mas é expandido automaticamente para cobrir a janela.
- `detection`: veja detalhes abaixo. `enabled: false` desliga a IA em todas as
  câmeras; `confidence` padrão é `0.25` e `fps` é a taxa de amostragem (não altera
  o vídeo exibido). `classes` controla o que aparece no player — deixe apenas
  `person` para esconder carros/motos.
- `face_confidence`: corte do YuNet para aceitar um rosto. `crop_queue_size` é o
  máximo de fotos por faixa lateral; `crop_attempts` quantas tentativas por track
  antes de desistir daquele rosto/placa.
- `detection_enabled`: opcional por câmera (`true`/`false`); quando ausente, usa
  `detection.enabled`. Câmeras adicionadas pela interface também recebem um
  worker de detecção automaticamente.
- Várias câmeras: use o botão **+ Adicionar câmera** na interface ou adicione
  objetos em `cameras` com `id` único diretamente no arquivo.

> **Modelos e eventos:** `models/yolov9t.onnx` (YOLO) e
> `models/face_detection_yunet_2023mar.onnx` (YuNet, para rostos) são baixados uma
> vez na primeira execução. Eventos deduplicados por `track_id`, snapshots
> anotados, recortes `face_*.jpg` e `plate_*.jpg` ficam em `events/<id>/`. O
> recorte de placa é uma heurística local (sem OCR) — ele tenta encontrar o
> retângulo e salva a imagem para uso futuro, sem ler os caracteres. Se `cv2` ou
> `numpy` não estiverem instalados, o viewer continua funcionando sem IA. O
> modelo YOLO upstream é **AGPL-3.0** e não é incluído no pacote; YuNet e OC-SORT
> são **Apache-2.0** e **MIT**, respectivamente.

> **Espaço em disco:** com `video_bitrate` de `2500k`, 30 minutos ocupam cerca de
> **560 MB por câmera** (≈ 0,31 MB/s). O FFmpeg apaga os segmentos fora da janela
> automaticamente; não é necessário limpar nada à mão.

### `secrets.json`

```json
{
  "camera_190_password": "SUA_SENHA_AQUI"
}
```

```bash
chmod 600 secrets.json
```

A senha é codificada na URL automaticamente com `urllib.parse.quote(..., safe="")`,
então `@`, `:`, `/` etc. funcionam sem edição manual (ex.: `@` vira `%40`).
Alternativa sem arquivo: `YV_SECRET_CAMERA_190_PASSWORD=... ./start.sh`
(a variável tem precedência sobre o JSON).

---

## 4. Executar

```bash
cd live_viewer
./start.sh
# ou, na raiz do Central, use python app.py (o viewer sobe como sidecar)
# ou: python3 app.py
```

Abra: **http://127.0.0.1:8088/**

Opções:

```bash
python3 app.py --host 0.0.0.0 --port 8088   # expor na LAN (veja segurança)
python3 app.py --log-level DEBUG
python3 app.py --config outro.json --secrets outro-secrets.json
```

Encerrar com segurança: `Ctrl+C` ou `kill -TERM "$(cat streams/.viewer.lock)"`.
O app encerra o HTTP, para os FFmpeg e remove os PID files.

Rotas úteis:

| Rota | Descrição |
|---|---|
| `GET /` | página do player |
| `GET /healthz` | health check com estado resumido |
| `GET /api/status` | JSON detalhado por câmera (estado, PID, idades, restarts, janela de replay) |
| `GET /api/cameras/<id>` | configuração completa da câmera, inclusive senha, para edição |
| `GET /api/detections` | estado da IA por câmera (caixas, contadores, erro do modelo) |
| `GET /api/detections/<id>` | estado de detecção de uma câmera |
| `GET /api/events?camera=<id>&limit=50` | eventos recentes deduplicados, com URL do snapshot |
| `GET /events/<id>/<arquivo>.jpg` | snapshot anotado, recorte `face_*.jpg` ou `plate_*.jpg` |
| `POST /api/cameras` | cadastra uma câmera (JSON) e inicia stream + IA (201) |
| `POST /api/cameras/<id>/rename` | altera o nome amigável (JSON) |
| `POST /api/cameras/<id>/update` | salva a configuração editada e reinicia o stream (JSON) |
| `POST /api/cameras/<id>/remove` | remove a câmera, stream, IA, senha e eventos |
| `POST /api/cameras/<id>/restart` | reinicia o stream daquela câmera (202) |
| `GET /streams/<id>/stream.m3u8` | playlist HLS |
| `GET /streams/<id>/seg_*.ts` | segmentos (suporta `Range`) |

---

## 5. Como funciona

1. O supervisor testa TCP `host:554` antes de subir o FFmpeg.
2. FFmpeg lê RTSP/UDP, decodifica HEVC e gera H.264 `yuv420p` (Main) + AAC mono.
3. A saída HLS usa segmentos de 2 s, `delete_segments` e `temp_file` (playlist só
   aparece pronta, nunca truncada). Com replay ativo, a playlist mantém as entradas
   necessárias para 30 min (900 segmentos) e usa `append_list`, então reinícios do
   FFmpeg **não apagam** o histórico. Um `#EXT-X-DISCONTINUITY` é inserido no
   reinício.
4. Na subida de cada FFmpeg, a playlist é podada: entradas mais antigas que a janela
   de replay são removidas (inclusive após a câmera ficar offline por muito tempo),
   garantindo que o navegador nunca ofereça mais de 30 min.
5. O supervisor acompanha `stream.m3u8`:
   - processo morreu → reinicia;
   - playlist sem atualização por `stall_timeout_seconds` → reinicia;
   - câmera inacessível (checagem a cada 10 s) → reinicia;
   - backoff exponencial de 1 s até 30 s, zerado após 30 s estável.
6. Um **lock de instância** (`streams/.viewer.lock`, `flock`) impede duas cópias do app.
7. Na inicialização, um PID file órfão só é encerrado se o `/proc/<pid>/cmdline`
   apontar para o diretório desta câmera neste projeto — nunca mata processo alheio.
8. Latência típica no navegador: **~4–6 s** (natureza do HLS).
9. Quando `detection.enabled` está ativo, um worker por câmera lê essa mesma
   playlist HLS com FFmpeg em `fps` amostras/segundo (padrão `2`) e redimensiona
   para `max_width` (padrão `960`) antes da inferência. Não há conexão RTSP extra.
10. Cada frame passa por letterbox → YOLOv9-t ONNX (`cv2.dnn`) → NMS por classe →
    OC-SORT. Só as caixas de `person` são publicadas em `/api/detections` e
    desenhadas (sem texto/percentual) em um `<canvas>` sobre o `<video>`.
11. Em paralelo, YuNet procura rostos dentro das caixas de pessoas e uma
    heurística de contornos procura placas dentro das caixas de veículos. Os
    recortes viram `face_*.jpg`/`plate_*.jpg` e alimentam as filas esquerda/direita
    do player; quando a fila passa de `crop_queue_size`, o mais antigo é apagado.
12. Uma pessoa nova (`track_id` ainda não visto) gera no máximo um evento por
    `event_cooldown_seconds`, com snapshot anotada em `events/<id>/`. O overlay
    some se o estado ficar parado por mais de 6 s.

### Replay no navegador

A barra abaixo do vídeo mostra `AO VIVO` à direita. Arraste para a esquerda para
voltar até 30 minutos; o tempo aparece como `-mm:ss`. Para voltar ao tempo real,
clique em **Voltar ao vivo** (ou no rótulo laranja do replay). O hls.js usa a própria
janela da playlist como DVR, sem baixar os 30 min de uma vez: só os trechos que
você pedir são carregados. O máximo do controle vem de `/api/status`
(`replay_seconds` e `history_seconds`), então a interface nunca oferece mais do
que existe de fato. Durante o replay a escala da barra fica fixa: o cursor não
"anda" sozinho na direção do ao vivo enquanto o histórico continua crescendo.

---

## 6. Segurança

- O servidor escuta em `127.0.0.1` por padrão. Só use `--host 0.0.0.0` em rede
  confiável; não há autenticação HTTP.
- `secrets.json` tem modo `600` e **não** está sob `/static` nem `/streams`; rotas
  tentando acessá-lo (inclusive `../`) retornam 404.
- Logs do app e do FFmpeg passam por redação: a senha (crua e URL-encoded) vira `***`.
- **Limitação inerente:** a URL RTSP completa aparece no `argv` do processo FFmpeg.
  Outro usuário do mesmo sistema com permissão de ler `/proc` pode vê-la. Mitigações:
  rodar o viewer com usuário dedicado, ou usar `hidepid` no `/proc`.
- Se a senha já foi exposta em chat/terminal/histórico, **troque a senha da câmera**.
- CSP restritiva, `nosniff`, `no-referrer`, `X-Frame-Options: SAMEORIGIN`, sem CORS.
- A página e o `/api/status` nunca incluem a senha (a URL é exibida como `admin:***`).
- Snapshots, rostos e placas em `events/<id>/` são servidos por HTTP sem
  autenticação, assim como os segmentos HLS. Mantenha o viewer em rede confiável;
  desative `detection.snapshot` se não quiser guardar imagens.
- Rostos/placas são só recortes locais: **não há reconhecimento facial, OCR nem
  banco de identidades**. Use essa função apenas onde você tenha autorização para
  monitorar o local.

---

## 7. Testes realizados (resultados reais)

Ambiente: Ubuntu, Python 3.14.4, FFmpeg com libx264/AAC, Chrome headless via CDP,
câmera `10.0.0.190` em `/onvif1` UDP.

| Teste | Resultado |
|---|---|
| FFmpeg conecta e gera HLS | ✅ playlist + segmentos de ~2 s |
| Playlist por HTTP | ✅ `200 application/vnd.apple.mpegurl`, sem cache |
| Playlist avança | ✅ `MEDIA-SEQUENCE` 7→9 em 6 s |
| Codecs/resolução (ffprobe na URL HTTP) | ✅ H.264 Main 1280×720 @ 15 fps + AAC LC mono 16 kHz |
| Reprodução real no Chrome | ✅ `readyState=4`, `currentTime` +5,31 s em 5 s, sem `MediaError` |
| Overlay/estado na UI | ✅ “ao vivo”, overlay oculto, 1280×720 |
| Queda do FFmpeg (SIGKILL) | ✅ novo PID em ~6 s, `restarts=1`, HLS retomado |
| Reinício pela API | ✅ HTTP 202, novo PID, `restarts` incrementa |
| Segunda instância do app | ✅ recusada com código 1 **sem matar** o FFmpeg da primeira |
| Órfão após SIGKILL do app | ✅ FFmpeg antigo encerrado na subida seguinte, 1 processo novo |
| Processos duplicados | ✅ sempre 1 FFmpeg por câmera (contagem via `/proc`) |
| Retenção de arquivos (sem replay) | ✅ estável em 8 segmentos (~5,2 MB), sem crescimento |
| Replay / DVR | ✅ câmera real, Chrome e Firefox: arrasto real de 15 s ficou estável por 12 s; "Voltar ao vivo" retorna ao tempo real |
| Reinício do FFmpeg com histórico | ✅ `append_list` preserva playlist/numeração e adiciona discontinuity |
| Limite de 30 min | ✅ configuração > 30 é limitada a 30; playlist antiga é podada na subida |
| Shutdown (SIGTERM) | ✅ app e FFmpeg encerrados, porta liberada, 0 órfãos |
| Rotas sensíveis | ✅ `/secrets.json`, `.ffmpeg.pid`, `../` → 404 |
| Vazamento de senha | ✅ 0 ocorrências em HTML/JS/HLS/logs; logs mostram `admin:***` |
| YOLOv9-t + OC-SORT em imagem real | ✅ tracks estáveis; player mostra só `person` |
| `DetectionManager` com HLS sintético | ✅ worker ativo, caixas de pessoas publicadas, evento com snapshot anotada |
| Recorte de placa | ✅ detectado em imagem sintética de veículo com placa |

Observação: o firmware envia pacotes RTP perdidos e NALUs HEVC inválidos
(`missed packets`, `PPS id out of range`). São avisos do FFmpeg; a imagem
permanece estável e o supervisor só reinicia em stall real (15 s).

---

## 8. Limitações conhecidas

- HLS não é tempo real: ~4–6 s de atraso.
- UDP pode perder pacotes; o firmware já emite avisos no log. Não foi possível
  validar TCP neste modelo (o handshake RTSP TCP falha).
- Áudio depende de a câmera enviar G.711; sem áudio, o FFmpeg segue só com vídeo.
- O vídeo é recodificado (CPU). Para muitas câmeras, considere `-preset` mais rápido
  ou hardware (`h264_nvenc`/`h264_vaapi`), ajustando `ffmpeg_manager.py`.
- Replay consome disco proporcional ao bitrate: ~560 MB/câmera para 30 min a 2500k.
  Reduza `replay_minutes` ou `video_bitrate` se o espaço for apertado.
- Se a câmera ficar offline por mais de 30 min, o histórico antigo é descartado na
  reconexão (não há conteúdo dentro da janela para preservar).
- A detecção roda em CPU por padrão e consome CPU extra por câmera; reduza `fps`,
  `max_width` e `input_size` se necessário, ou use `prefer_cuda` com um OpenCV
  compatível. Sem `numpy`/`opencv-python-headless`, a IA é ignorada.
- O modelo é treinado em COCO: pode confundir pessoas com objetos parecidos.
  `confidence` e `event_cooldown_seconds` servem para ajustar falsos positivos.
- YuNet precisa de rosto razoavelmente visível: em câmeras muito distantes ou com
  pessoas de costas, a faixa de rostos pode ficar vazia. A placa é apenas um
  recorte heurístico (sem OCR), então pode errar ou não encontrar em movimento.
- A estimativa de velocidade dos veículos ainda não está implementada; os
  recortes de placa ficam salvos para servir de base a essa etapa futura.
- Sem autenticação de usuários; é um visualizador local.

---

## 9. Integração com o Central Cameras (Flask)

O viewer já vive em `live_viewer/` dentro do Central e sobe como sidecar a partir
do `app.py` da raiz. O Flask faz proxy de `/`, `/api/*`, `/events/*` e
`/streams/*` em `/ao-vivo`, repassando `X-Forwarded-Prefix: /ao-vivo`; por isso
as URLs relativas do player continuam funcionando.

- `CameraManager`, `DetectionManager`, os FFmpeg e os workers de IA rodam no
  processo do viewer, nunca por worker web. Reiniciar o Flask não derruba o
  vídeo se o viewer já estiver no ar.
- As classes Python não dependem de Flask e podem ser usadas de forma
  autônoma; basta garantir `manager.stop()` e `detection.stop()` no shutdown.
- Câmeras adicionadas ou renomeadas pela interface são refletidas também no
  detector, sem reiniciar o viewer.
- `secrets.json` continua fora das rotas públicas e o lock de instância evita
  duplicar FFmpeg/detecção.

Serviço systemd de exemplo (opcional, para rodar fora do Central):

```ini
[Unit]
Description=LYSec Play | Live Viewer (RTSP -> HLS + visão computacional)
After=network-online.target

[Service]
Type=simple
User=leonardo
WorkingDirectory=/caminho/para/central_cameras/live_viewer
ExecStart=/usr/bin/python3 /caminho/para/central_cameras/live_viewer/app.py
Restart=on-failure
RestartSec=5
KillSignal=SIGTERM
TimeoutStopSec=20

[Install]
WantedBy=multi-user.target
```

---

## 10. Diagnóstico rápido

```bash
# estado e saúde
curl -s http://127.0.0.1:8088/healthz
curl -s http://127.0.0.1:8088/api/status

# visão computacional
curl -s http://127.0.0.1:8088/api/detections
curl -s 'http://127.0.0.1:8088/api/events?camera=yoosee-190&limit=10'

# playlist e codecs (a URL não contém credenciais)
curl -s http://127.0.0.1:8088/streams/yoosee-190/stream.m3u8
ffprobe -v error -show_entries stream=codec_name,width,height,avg_frame_rate \
  http://127.0.0.1:8088/streams/yoosee-190/stream.m3u8

# logs (já redigidos)
tail -f logs/viewer.log
tail -f logs/ffmpeg-yoosee-190.log

# parar com segurança
kill -TERM "$(cat streams/.viewer.lock)"
```

Se a câmera estiver offline, a UI mostra “Câmera desconectada” e o supervisor
continua tentando com backoff, sem acumular processos.
