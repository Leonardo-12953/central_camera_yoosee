# LYSec Play | Live Viewer

Visualizador independente que converte o RTSP de câmeras Yoosee em **HLS** reproduzível
no navegador. Feito para testar a ideia antes de integrar ao projeto **Central Cameras**.

- Python **somente biblioteca padrão** (nenhum pacote pip).
- **FFmpeg** faz a conversão: H.265/HEVC → H.264, G.711 → AAC.
- **Um processo FFmpeg por câmera**, supervisionado e reiniciado automaticamente.
- Página responsiva com player, estado da conexão e botão de reinício.
- **Replay de até 30 minutos**: arraste a barra abaixo do vídeo para voltar no tempo.
- Credenciais fora dos arquivos públicos, logs com senha redigida.
- HLS com retenção limitada: segmentos antigos são apagados automaticamente.

---

## 1. Requisitos

| Dependência | Por quê | Como verificar |
|---|---|---|
| Python 3.10+ | servidor HTTP, supervisor e API (stdlib) | `python3 --version` |
| FFmpeg com `libx264` e `aac` | decodificar HEVC e gerar HLS H.264/AAC | `ffmpeg -hide_banner -encoders \| grep -E "libx264|aac "` |
| Navegador com MSE (Chrome/Edge/Firefox) ou HLS nativo (Safari) | reproduzir HLS | — |

O `hls.js` 1.5.17 já está vendorizado em `static/hls.min.js`; **não** é baixado em tempo
de execução e não há dependência de CDN.

Nada é instalado globalmente por este projeto. Se o FFmpeg não estiver presente:

```bash
sudo apt install ffmpeg        # Ubuntu/Debian — requer sua autorização
```

---

## 2. Estrutura

```
yoosee_viewer/
├── app.py               # servidor HTTP, rotas, página, sinais, lock de instância
├── camera_manager.py    # config/segredos + supervisor (1 thread por câmera)
├── camera_store.py      # valida e grava câmeras adicionadas pela interface
├── ffmpeg_manager.py    # comando FFmpeg, processo, PID file, limpeza de órfãos
├── logging_utils.py     # log rotativo com redação de credenciais
├── config.json          # câmeras e parâmetros HLS (sem senha)
├── secrets.json         # senhas (modo 600, fora do que é servido)
├── templates/index.html       # página com {{CAMERA_CARDS}}
├── templates/camera_card.html # markup de cada cartão (botões + logs)
├── static/player.js     # player hls.js, polling de status, reconexão de UI
├── static/cameras.js    # formulário "Adicionar câmera"
├── static/style.css
├── static/hls.min.js    # biblioteca vendorizada
├── streams/<id>/        # playlist .m3u8 + segmentos .ts + .ffmpeg.pid
├── logs/                # viewer.log + ffmpeg-<id>.log (rotativos)
├── requirements.txt     # documenta "zero dependências Python"
└── start.sh
```

`secrets.json`, `streams/*` e `logs/*` estão no `.gitignore`.

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
- Várias câmeras: use o botão **+ Adicionar câmera** na interface ou adicione
  objetos em `cameras` com `id` único diretamente no arquivo.

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
cd /home/leonardo/yoosee_viewer
./start.sh
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
- Sem autenticação de usuários; é um visualizador local.

---

## 9. Integração futura com o Central Cameras (Flask)

O viewer foi mantido desacoplado de propósito. Opções:

1. **Rodar como serviço separado** (recomendado): o Flask faz proxy/iframe para
   `http://127.0.0.1:8088/` ou consome `/api/status`, mantendo o FFmpeg fora do
   processo web. Vantagem: reinício do Flask não derruba o vídeo.
2. **Importar os módulos**: `CameraManager` não depende de Flask; pode ser
   instanciado no `app.py` do Flask. Nesse caso, garanta `manager.stop()` no shutdown.
3. **Reaproveitar só o front-end**: `templates/index.html` + `static/player.js`
   esperam cartões com `data-camera` e `data-hls`, e o endpoint `/api/status`.

Pontos de atenção na integração:
- não servir `secrets.json` pelo Flask;
- manter o lock de instância para não duplicar FFmpeg;
- se usar Gunicorn/uWSGI com múltiplos workers, o supervisor deve rodar em **um**
  processo dedicado (ou fora do servidor web), nunca por worker.

Serviço systemd de exemplo (opcional):

```ini
[Unit]
Description=LYSec Play | Live Viewer (RTSP -> HLS)
After=network-online.target

[Service]
Type=simple
User=leonardo
WorkingDirectory=/home/leonardo/yoosee_viewer
ExecStart=/usr/bin/python3 /home/leonardo/yoosee_viewer/app.py
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
