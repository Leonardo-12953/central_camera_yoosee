# Central de Câmeras — guia rápido

Este pacote já vem pronto. **Não é necessário instalar Python, Git ou qualquer
outro programa.**

O programa é distribuído como **pasta**: mantenha `CentralCameras.exe` (ou
`CentralCameras` no Linux) junto da pasta `_internal`.

## 1. Abrir o programa

### Windows

1. Extraia o `.zip` em uma pasta comum, como `Documentos` ou `Desktop`.
2. Dê dois cliques em `CentralCameras.exe`.
3. O navegador abre sozinho.
4. Mantenha a janela preta aberta enquanto estiver usando. Para encerrar, feche
   essa janela ou pressione `Ctrl+C`.

> Se aparecer “O Windows protegeu o computador”, clique em **Mais informações**
> → **Executar assim mesmo**.

### Linux

```bash
chmod +x CentralCameras
./CentralCameras
```

O navegador abre automaticamente. Mantenha o terminal aberto enquanto usa.

## 2. Criar o acesso (primeiro uso)

Na primeira vez, o navegador mostra a tela **Criar acesso**:

1. Escolha um usuário (pode deixar `admin`).
2. Crie uma senha com pelo menos 8 caracteres.
3. Confirme a senha.
4. Clique em **Criar acesso e continuar**.

Essa senha será pedida sempre que alguém abrir o programa. Guarde-a.

## 3. Configurar a pasta das gravações

1. Clique em **Configurações**.
2. Cole o caminho completo da pasta onde as câmeras salvam os vídeos.
   - Windows: abra a pasta no Explorador, clique na barra de endereço e copie.
     Exemplo: `D:\Gravacoes`.
   - Linux: exemplo `/mnt/hd/gravacoes`.
3. Escolha por quantos dias as gravações serão guardadas (mínimo 2 dias).
4. Clique em **Salvar configurações**.

## 4. Ver as gravações

Na tela inicial, clique em **Ver gravações** na câmera desejada. Você pode:

- assistir à prévia no navegador;
- clicar em **Abrir** para abrir o vídeo em outra aba;
- clicar em **Baixar** para salvar o arquivo;
- buscar por data ou por parte do nome do arquivo.

Se a prévia não aparecer, use **Abrir** ou **Baixar**. Alguns navegadores não
reproduzem vídeos em H.265/HEVC; o arquivo baixado abre no VLC ou no player do
Windows.

## Acessar pelo celular ou outro computador

Na tela inicial aparece o endereço de acesso pela rede, por exemplo:

```text
http://192.168.0.10:5000/
```

O celular ou outro computador precisa estar na **mesma rede**. O login será
pedido normalmente.

Se não abrir, libere a porta `5000` no firewall do Windows (rede privada) ou do
roteador.

## Alterar ou recuperar a senha

- Para trocar a senha: entre no programa e clique em **Alterar senha**.
- Para recuperar a senha: na tela de login, no **próprio computador**, clique
  em **Esqueci a senha** e crie uma nova.

A recuperação não funciona por outro aparelho da rede, por segurança.

## Arquivos importantes

| Arquivo | Conteúdo |
|---|---|
| `config.json` | Pasta das gravações, dias de retenção e nomes das câmeras. |
| `auth.json` | Usuário e senha (senha protegida por hash). |
| `.secret_key` | Chave interna de sessão. |
| `central_cameras.log` | Registro de funcionamento e erros. |

Se a pasta do programa não permitir gravação, o programa usa automaticamente
`%APPDATA%\CentralCameras` no Windows.

**Backup:** copie `config.json` e `auth.json` para um lugar seguro.

## Problemas comuns

- **O navegador não abriu:** abra manualmente `http://127.0.0.1:5000`.
- **A porta 5000 está ocupada:** o programa usa a próxima porta livre e mostra o
  endereço correto na janela.
- **Antivírus bloqueou:** adicione a pasta do programa às exceções.
- **O Windows bloqueou o arquivo:** botão direito → **Propriedades** →
  **Desbloquear** → **Aplicar**.
- **Erro ao abrir:** a pasta `_internal` foi separada do executável. Extraia o
  `.zip` novamente e mantenha a pasta inteira.
- **Quero atualizar:** substitua o executável **e** a pasta `_internal` juntos.
  Mantenha `config.json` e `auth.json`.
