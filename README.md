# 📹 LYSec Play

Central de câmeras Yoosee no navegador: organiza as gravações que o Yoosee
Desktop salva no computador e transforma aqueles nomes incompreensíveis em
câmeras, datas e horários legíveis.

## Por que existe

Eu tinha 4 câmeras Yoosee e nenhum cartão de memória. A solução foi instalar o
Yoosee Desktop no Windows e gravar direto no HD da máquina — o armazenamento
estava resolvido, mas achar as gravações virou um problema.

O Yoosee salvava tudo em pastas e arquivos com IDs numéricos enormes, e esses
nomes não podiam ser alterados sem risco de quebrar o funcionamento do programa.
Na prática, encontrar o vídeo de um horário específico era garimpar no escuro.

Foi daí que surgiu o LYSec Play: ele lê a estrutura original do Yoosee sem
mexer em nada e traduz os IDs para nomes de câmera e para data/hora de verdade.
O Yoosee continua funcionando normalmente; quem ganha uma interface simples é
você.

## O que ele faz

- Dá nomes fáceis às câmeras (“Portão”, “Garagem”…) sem renomear as pastas
  originais.
- Converte os nomes dos arquivos em data e hora legíveis.
- Permite assistir, abrir e baixar as gravações pelo navegador.
- Busca gravações por data ou por parte do nome do arquivo.
- Apaga automaticamente as gravações mais antigas que o prazo configurado.
- Exige login e pode ser acessado pelo celular ou por outro computador da mesma
  rede.
- Na aba **Ao vivo**, identifica pessoas, desenha as caixas sobre o vídeo, salva
  eventos com snapshot e mostra recortes de rostos/placas nas laterais.

> O programa **não grava** as câmeras. Ele organiza e exibe as gravações que o
> Yoosee Desktop já salva no computador.

![Tela inicial](assets/img_homepage.png)

![Gravações de uma câmera](assets/img_feed.png)

## Como rodar

Baixe o pacote mais recente na página de
[Releases](https://github.com/Leonardo-12953/central_camera_yoosee/releases).
Não é preciso instalar Python nem usar o terminal.

**Windows:** extraia o `.zip` e dê dois cliques em `CentralCameras.exe`.

**Linux:**

```bash
chmod +x CentralCameras
./CentralCameras
```

O navegador abre sozinho. No primeiro acesso, crie o usuário e a senha e informe
a pasta onde o Yoosee salva as gravações. Pronto.

Passo a passo completo, com senha, arquivos e problemas comuns:
[COMO_USAR.md](COMO_USAR.md).

## Acessar pelo celular ou outro computador

O programa já fica disponível na rede local. Use o endereço mostrado na tela
inicial — algo como `http://192.168.0.10:5000/` — em um aparelho conectado à
mesma rede. Se não abrir, libere a porta `5000` no firewall.

## Visualização ao vivo (LYSec Play | Live Viewer)

O painel inclui a aba **Ao vivo**, servida pelo Live Viewer (RTSP → HLS), que
agora faz parte desta mesma pasta em `live_viewer/`. Basta iniciar o Central:
ele sobe o viewer automaticamente em `http://127.0.0.1:8088` e o encerra junto
com o Central.

- A pasta `live_viewer/` está dentro do Central, então o sistema inteiro é
  apenas **uma pasta**. O projeto antigo `yoosee_viewer/` pode ficar onde está
  como referência/backup; o Central não o usa mais.
- Pela interface do viewer dá para **adicionar câmeras**, **alterar o nome**,
  **editar todas as configurações de acesso** pela engrenagem no cartão e
  **remover câmeras**, sem editar arquivos. A edição mostra inclusive a senha e o
  `source_url`, salva a alteração e reinicia o stream daquela câmera. A remoção
  encerra o stream e a IA, apaga a senha salva, os eventos e os snapshots dela;
  as demais câmeras continuam funcionando.
- Também há uma **câmera pública de teste** (`pub-copacabana`, Copacabana/RJ,
  <https://worldwatcher.live/cam/AYcY3pIV>) cadastrada com `source_url` no
  `live_viewer/config.json`. Ela usa `yt-dlp` para ler a transmissão do YouTube
  e serve para testar o player e a detecção sem depender das câmeras locais.
  Para adicionar outra, use **Adicionar câmera → Câmera pública**: só precisa do
  nome e do link `https://www.youtube.com/watch?v=...` de uma transmissão ao vivo
  (ou de uma URL `.m3u8`). O viewer preenche o resto automaticamente.
- Se o viewer já estiver em execução, o Central apenas se conecta a ele e não
  interfere no processo existente.
- Para usar um viewer em outra máquina, desmarque o início automático e ajuste
  a URL do serviço nas configurações.
- O log do processo iniciado pelo Central fica em
  `live_viewer/logs/central_sidecar.log`.

### Visão computacional no Ao vivo

O viewer identifica **pessoas** usando YOLOv9-t em ONNX (`cv2.dnn`) e OC-SORT
para manter o `track_id` entre frames. Veículos continuam sendo detectados só
internamente, para tentar capturar a placa. Ele **não abre uma segunda conexão
RTSP**: um FFmpeg auxiliar lê a própria playlist HLS do viewer, e as caixas são
desenhadas em um `<canvas>` sobre o player, sem recodificar o vídeo.

- Só pessoas aparecem no overlay, sem nome de classe nem percentual. A faixa
  **esquerda** mostra rostos (YuNet) e a **direita** mostra placas (heurística
  local, sem OCR), em fila: ao encher, o mais antigo sai. É captura local de
  imagem — não há reconhecimento facial, OCR nem banco de identidades.
- A configuração fica em `detection` dentro de `live_viewer/config.json`:
  `enabled`, `model`, `model_url`, `input_size`, `confidence` (padrão `0.25`),
  `iou`, `fps` (amostragem, padrão `2`), `max_width`, `classes` (deixe `person`),
  `max_age`, `min_track_len`, `event_cooldown_seconds`, `snapshot`, `prefer_cuda`,
  `face_model`, `face_model_url`, `face_confidence`, `crop_queue_size` e
  `crop_attempts`.
- Para desativar em uma câmera específica, adicione `"detection_enabled": false`
  ao objeto dela em `cameras`. Para desativar em todas, use
  `"detection": { "enabled": false }`.
- Os modelos são baixados uma vez: YOLOv9 do
  [Kalray/yolov9](https://huggingface.co/Kalray/yolov9/resolve/main/yolov9t.onnx)
  e YuNet do
  [OpenCV Zoo](https://github.com/opencv/opencv_zoo/raw/main/models/face_detection_yunet/face_detection_yunet_2023mar.onnx),
  ambos em `live_viewer/models/`. Eventos deduplicados por `track_id`, snapshots
  anotados e recortes `face_*.jpg`/`plate_*.jpg` ficam em
  `live_viewer/events/<id-da-camera>/`.
- A estimativa de velocidade dos veículos ainda não está pronta; os recortes de
  placa ficam salvos para servir de base a essa etapa futura.
- A interface consulta `GET /api/detections` (estado por câmera) e
  `GET /api/events` (histórico recente, com URL do snapshot).
- As dependências `numpy` e `opencv-python-headless` já estão em
  `requirements.txt`. Se elas faltarem ou o modelo não puder ser obtido, o
  player continua funcionando normalmente — apenas a IA fica indisponível.
- O modelo YOLO ONNX é distribuído pelo autor sob **AGPL-3.0**; ele não é incluído
  no zip e é baixado em tempo de execução. YuNet é **Apache-2.0** e o rastreador
  OC-SORT adaptado é **MIT** (créditos em `live_viewer/vision/tracker/__init__.py`).

**Para distribuir:** envie a pasta `central_cameras/` inteira, mas **remova
`live_viewer/secrets.json` antes** — as senhas ficam lá em texto puro (permissão
`600`) e não devem acompanhar o pacote. No computador de destino, crie
`live_viewer/secrets.json` com as chaves usadas em `live_viewer/config.json`.
Os segmentos HLS em `live_viewer/streams/`, os logs, `live_viewer/models/` e
`live_viewer/events/` são gerados em execução e não precisam ser enviados.
Para a IA funcionar no pacote executável, o Python usado pelo viewer também
precisa ter `numpy` e `opencv-python-headless` instalados (ou defina
`LIVE_VIEWER_PYTHON` apontando para esse interpretador).

> No modo executável empacotado, o início automático ainda depende de um Python
> disponível no sistema. A integração completa no executável está prevista como
> etapa seguinte.

## Rodar a partir do código

```bash
python3 -m venv venv
source venv/bin/activate        # Windows: .\venv\Scripts\Activate.ps1
pip install -r requirements.txt
python app.py
```

Acesse `http://127.0.0.1:5000`. O servidor usa **Waitress** por padrão; para
desenvolvimento com recarga automática, use `FLASK_DEBUG=1`.

Para gerar os executáveis:

```bat
build_windows.bat
```

```bash
./build_linux.sh
```

Os pacotes saem em `dist/`. Opções avançadas de rede, sessão e limpeza estão
documentadas no [.env.example](.env.example).
