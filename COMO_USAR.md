# Central de Câmeras — guia rápido

Este pacote já vem com tudo o que precisa para rodar. **Não é necessário instalar Python, Git ou qualquer outro programa.**

## Windows

1. Extraia o arquivo `.zip` em uma pasta comum, como `Documentos` ou `Desktop`.
   - Evite `C:\Program Files`, pois a pasta pode não permitir gravação.
2. Dê dois cliques em `CentralCameras.exe`.
3. O navegador abrirá sozinho em `http://127.0.0.1:5000`.
4. Mantenha a janela preta aberta enquanto estiver usando. Para encerrar, feche essa janela ou pressione `Ctrl+C`.

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

## Primeiro acesso

1. Abra **⚙ CONFIGURAÇÕES**.
2. Informe o caminho da pasta onde as câmeras gravam os vídeos.
3. Ajuste os dias de retenção.
4. Clique em **Salvar Configurações**.

Se o pacote foi preparado com login, aparecerá a tela de acesso. Use o usuário e a senha fornecidos por quem preparou o pacote.

## Onde ficam os arquivos

- `config.json`: configurações do sistema e nomes amigáveis das câmeras.
- `.env`: login e opções de rede (se incluído).
- `.secret_key`: chave interna de sessão.
- `central_cameras.log`: registro de funcionamento e erros.

Por padrão, esses arquivos ficam na mesma pasta do executável. Se a pasta não permitir gravação, o programa usa automaticamente uma pasta de dados do usuário e mostra o caminho na janela ao iniciar.

## Acessar de outro computador ou celular

O pacote inclui o modelo `.env.example`. Renomeie/copie esse arquivo para `.env` e ajuste:

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
- **Esqueci a senha:** quem administra o sistema pode gerar outro hash e atualizar o `.env` (veja o README do projeto).
- **Quero atualizar:** substitua apenas o executável e mantenha `config.json`, `.env` e `.secret_key`.

## Backup

Copie `config.json` e `.env` para um local seguro. Eles guardam as configurações e o acesso ao sistema.
