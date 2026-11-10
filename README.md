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
