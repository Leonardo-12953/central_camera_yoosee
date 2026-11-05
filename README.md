# 📹 Central de Câmeras

Aplicativo pronto para **Windows e Linux** que organiza as gravações das
câmeras Yoosee e permite assistir aos vídeos pelo navegador — no computador,
no celular ou em outro computador da mesma rede.

**Você não precisa instalar Python nem saber usar o terminal.**

---

## O que o programa faz

- Mostra as câmeras com nomes fáceis de entender, como “Portão” ou “Garagem”.
- Converte os nomes complicados dos arquivos em data e hora legíveis.
- Permite assistir, abrir e baixar as gravações pelo navegador.
- Busca gravações por data ou por nome do arquivo.
- Apaga automaticamente as gravações mais antigas que o prazo configurado.
- Exige usuário e senha para proteger o acesso.

![Tela inicial](assets/img_homepage.png)

---

## Como usar no Windows

1. Baixe o arquivo `CentralCameras-windows.zip` na página de **Releases** do
   GitHub.
2. Clique com o botão direito no `.zip` → **Propriedades** → marque
   **Desbloquear** → **Aplicar**. Depois extraia o conteúdo em uma pasta comum,
   como `Documentos` ou `Desktop`.
3. Dê dois cliques em `CentralCameras.exe`.
4. O navegador abre sozinho. Na primeira vez, aparecerá a tela
   **“Criar acesso”**:
   - escolha um usuário (pode deixar `admin`);
   - crie uma senha com pelo menos 8 caracteres;
   - guarde essa senha.
5. Depois de criar o acesso, clique em **Configurações** e informe a pasta onde
   as câmeras salvam os vídeos.
6. Clique em **Salvar configurações**. Pronto.

> Mantenha a janela preta aberta enquanto estiver usando o programa. Para
> encerrar, feche essa janela ou pressione `Ctrl+C`.

> Se o Windows mostrar o aviso “O Windows protegeu o computador”, clique em
> **Mais informações** → **Executar assim mesmo**. Isso acontece porque o
> programa não tem assinatura digital paga.

---

## Como usar no Linux

1. Extraia o `CentralCameras-linux.zip`.
2. Abra o terminal na pasta e permita a execução:

   ```bash
   chmod +x CentralCameras
   ./CentralCameras
   ```

3. O navegador abre automaticamente. O primeiro acesso é igual ao do Windows:
   crie usuário e senha e configure a pasta das gravações.

---

## Acessar pelo celular ou por outro computador

O programa já fica disponível para a rede local. Na tela inicial aparece um
quadro chamado **“Acesse pelo celular ou por outro computador”**, com um
endereço parecido com:

```text
http://192.168.0.10:5000/
```

Digite esse endereço no celular ou no outro computador, desde que ele esteja na
**mesma rede Wi-Fi ou no mesmo roteador**. O login e a senha serão pedidos
normalmente.

Se outro aparelho não conseguir abrir:

- confirme que os dois estão na mesma rede;
- permita o programa no firewall do Windows (rede privada) ou libere a porta
  `5000`;
- use o endereço que aparece dentro do programa, e não um endereço antigo.

---

## Login e senha

- O login é **obrigatório**.
- A conta é criada no **primeiro acesso**, direto pela tela do navegador.
- Para trocar a senha, entre no programa e clique em **Alterar senha**.
- **Esqueceu a senha?** Abra o programa no mesmo computador onde ele está
  rodando, clique em **Esqueci a senha** na tela de login e crie uma nova.
  Por segurança, essa recuperação não funciona por outro aparelho da rede.

> Usuários que já usavam a versão antiga com `APP_USERNAME` e
> `APP_PASSWORD_HASH` no `.env` continuam conseguindo entrar. Depois de entrar,
> troque a senha em **Alterar senha** para que ela passe a ficar na conta nova.

---

## Onde ficam os arquivos

Normalmente, na mesma pasta do programa:

| Arquivo | Para que serve |
|---|---|
| `config.json` | Pasta das gravações, prazo de retenção e nomes das câmeras. |
| `auth.json` | Usuário e senha (a senha fica protegida por hash). |
| `.secret_key` | Chave interna de segurança da sessão. |
| `central_cameras.log` | Registro de funcionamento e erros. |

Se a pasta do programa não permitir gravação (por exemplo, `C:\Program
Files`), o programa usa automaticamente uma pasta de dados do usuário
(`%APPDATA%\CentralCameras` no Windows).

**Backup:** copie `config.json` e `auth.json` para um lugar seguro.

---

## Problemas comuns

| Problema | O que fazer |
|---|---|
| O navegador não abriu | Abra manualmente `http://127.0.0.1:5000`. |
| A porta 5000 está ocupada | O programa usa automaticamente a próxima porta livre e mostra o endereço na janela. |
| A prévia do vídeo não aparece ou fica preta | Algumas câmeras gravam em H.265/HEVC, que nem todo navegador reproduz. Clique em **Abrir** ou **Baixar** na gravação; o arquivo abre no VLC ou no player do Windows. |
| Os botões ou o visual não funcionam | Use a versão mais recente: os arquivos de estilo e script agora vão dentro do pacote. |
| Outro aparelho não abre a página | Verifique a rede e libere a porta 5000 no firewall. |
| Esqueci a senha | Na tela de login do próprio computador, clique em **Esqueci a senha**. |
| O Windows bloqueou o programa | Propriedades do arquivo → **Desbloquear** → **Aplicar**. |
| Antivírus reclamou | Adicione a pasta do programa às exceções. Executáveis feitos com PyInstaller às vezes são sinalizados sem motivo. |
| Atualizei e o programa não abre | Substitua o executável **e** a pasta `_internal` juntos. Mantenha `config.json` e `auth.json`. |

---

## Gerar o executável

O cliente final recebe uma pasta com o executável e a pasta `_internal`. Não
separe os dois.

**Windows:**

```bat
build_windows.bat
```

**Linux:**

```bash
chmod +x build_linux.sh
./build_linux.sh
```

Os pacotes são gerados em:

- `dist/CentralCameras-windows.zip`
- `dist/CentralCameras-linux.zip`

O PyInstaller não faz compilação cruzada: gere o `.exe` no Windows e o binário
Linux no Linux. Também é possível usar **Actions → Gerar executáveis → Run
workflow** no GitHub.

---

## Rodar a partir do código-fonte (opcional)

```bash
python3 -m venv venv
source venv/bin/activate        # Windows: .\venv\Scripts\Activate.ps1
pip install -r requirements.txt
python app.py
```

Abra `http://127.0.0.1:5000`. Na primeira execução, crie o acesso e configure a
pasta das gravações pela própria tela.

O servidor usa **Waitress** por padrão. Para desenvolvimento com recarga
automática, use `FLASK_DEBUG=1`.

<details>
<summary><strong>Opções avançadas (arquivo .env)</strong></summary>

Copie `.env.example` para `.env` e ajuste apenas o que precisar:

| Variável | Padrão | Descrição |
|---|---|---|
| `HOST` | `0.0.0.0` | `0.0.0.0` libera o acesso pela rede local. Use `127.0.0.1` para somente este computador. |
| `PORT` | `5000` | Porta inicial. Se estiver ocupada, a próxima livre é usada. |
| `OPEN_BROWSER` | `1` no executável | Abre o navegador automaticamente. |
| `AUTH_SESSION_LIFETIME_MINUTES` | `720` | Tempo que o login fica válido. |
| `LOGIN_MAX_ATTEMPTS` | `5` | Tentativas de login por IP antes do bloqueio temporário. |
| `SERVER_THREADS` | `8` | Threads do servidor. |
| `CLEANUP_INTERVAL_SECONDS` | `3600` | Intervalo da limpeza automática. |
| `LOG_LEVEL` | `INFO` | Nível do log (`DEBUG`, `INFO`, `WARNING`, `ERROR`). |

Para recuperar a senha pelo terminal (alternativa à tela):

```bat
CentralCameras.exe set-password
```

```bash
./CentralCameras set-password
```

Para simular a limpeza de gravações sem apagar nada:

```bat
CentralCameras.exe purge --dry-run
```

</details>

---

## Segurança

- O login é obrigatório e a senha é guardada com hash, nunca em texto puro.
- A sessão é assinada, tem proteção CSRF e limite de tentativas por IP.
- A recuperação de senha só funciona no próprio computador.
- Para acesso pela internet (fora da rede local), use HTTPS por meio de um
  proxy reverso e mantenha uma senha forte.
