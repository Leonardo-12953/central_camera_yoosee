# Central de Câmeras — guia rápido

Este pacote já vem com tudo o que precisa para rodar. **Não é necessário instalar Python, Git ou qualquer outro programa.**

O programa é distribuído como **pasta** (`CentralCameras.exe` + `_internal`), e não como arquivo único. Mantenha os dois juntos.

## Windows

1. Extraia o arquivo `.zip` em uma pasta comum, como `Documentos` ou `Desktop`.
   - Evite `C:\Program Files`, pois a pasta pode não permitir gravação.
2. Dê dois cliques em `CentralCameras.exe`.
3. O navegador abrirá sozinho em `http://127.0.0.1:5000`.
4. Mantenha a janela preta aberta enquanto estiver usando. Para encerrar, pressione `Ctrl+C` ou feche essa janela.

> Não mova o `CentralCameras.exe` para fora da pasta. Ele precisa da pasta `_internal` ao lado. Se for mover o programa, mova a pasta inteira.

Se o Windows mostrar um aviso de segurança do SmartScreen:

1. Clique em **Mais informações**.
2. Clique em **Executar assim mesmo**.

Isso acontece porque o executável não possui assinatura digital paga. O programa é o mesmo do projeto.

## Linux

1. Extraia o `.zip`.
2. Abra a pasta e permita execução:

   ```bash
   chmod +x CentralCameras
   ```

3. Execute com dois cliques ou pelo terminal:

   ```bash
   ./CentralCameras
   ```

4. O navegador abre automaticamente. Mantenha o terminal aberto enquanto usa.

## Configurar o login (recomendado)

O programa sai **sem login** por padrão. Para exigir usuário e senha:

1. Abra o Prompt de Comando (Windows) ou terminal (Linux) na pasta do programa.
2. Rode:

   ```bat
   CentralCameras.exe set-password
   ```

   ```bash
   ./CentralCameras set-password
   ```

3. Informe o usuário (Enter usa `admin`) e digite a senha duas vezes.
4. Reinicie o programa. A tela de login aparecerá.

O comando grava `APP_USERNAME` e `APP_PASSWORD_HASH` no arquivo `.env`; a senha não fica em texto puro. Se o login estiver desativado, o painel exibe um aviso amarelo — nunca use `HOST=0.0.0.0` sem login configurado.

> **Windows:** confira se o arquivo se chama exatamente `.env`, e não `.env.txt`. No Explorer, ative **Extensões de nomes de arquivos**.

## Primeiro acesso

1. Abra **⚙ CONFIGURAÇÕES**.
2. Informe o caminho da pasta onde as câmeras gravam os vídeos.
3. Ajuste os dias de retenção (mínimo **2 dias**).
4. Clique em **Salvar Configurações**.

## Limpeza automática

O programa apaga gravações mais antigas que a retenção configurada. A retenção mínima é de **2 dias** e não pode ser reduzida pela interface.

Para conferir o que seria apagado **sem apagar nada**:

```bat
CentralCameras.exe purge --dry-run
```

```bash
./CentralCameras purge --dry-run
```

Somente arquivos `.mp4` são considerados, e arquivos recém-gravados (menos de 10 minutos) são preservados.

## Onde ficam os arquivos

- `config.json`: configurações do sistema e nomes amigáveis das câmeras.
- `.env`: login e opções de rede (criado pelo `set-password`).
- `.secret_key`: chave interna de sessão.
- `central_cameras.log`: registro de funcionamento e erros.

Por padrão, esses arquivos ficam na mesma pasta do executável. Se a pasta não permitir gravação, o programa usa automaticamente uma pasta de dados do usuário e mostra o caminho na janela ao iniciar.

## Acessar de outro computador ou celular

Copie `.env.example` para `.env` e ajuste:

```env
HOST=0.0.0.0
```

Depois reinicie o programa e acesse pelo IP do computador na rede, por exemplo:

```text
http://192.168.0.10:5000
```

Use isso somente em uma rede confiável e **com o login configurado**. Pode ser necessário liberar a porta `5000` no firewall do sistema.

## Problemas comuns

- **O navegador não abriu:** abra manualmente `http://127.0.0.1:5000`.
- **A porta 5000 está em uso:** o programa tenta automaticamente as próximas portas e mostra o endereço correto na janela.
- **Antivírus bloqueou:** adicione a pasta do programa à lista de exceções. Executáveis feitos com PyInstaller às vezes são sinalizados de forma indevida.
- **“Uma política de Controle de Aplicativo bloqueou este arquivo” / “Extensão de arquivo perigosa do web”:** o Windows marcou o arquivo como baixado da internet (Smart App Control). Clique com o botão direito no `.zip` → **Propriedades** → **Desbloquear** → **Aplicar** antes de extrair. Se já extraiu, clique com o botão direito em `CentralCameras.exe` → **Propriedades** → **Desbloquear**. Não é necessário executar como administrador.
- **Esqueci a senha:** rode `CentralCameras.exe set-password` novamente.
- **Erro ao abrir / “app não encontrado”:** a pasta `_internal` foi separada do `.exe`. Extraia o `.zip` novamente e mantenha a pasta inteira.
- **Quero atualizar:** substitua o `CentralCameras.exe` **e** a pasta `_internal` juntos. Mantenha `config.json`, `.env` e `.secret_key`.

## Backup

Copie `config.json` e `.env` para um local seguro. Eles guardam as configurações e o acesso ao sistema.
