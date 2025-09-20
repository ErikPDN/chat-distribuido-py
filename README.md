# Chat Application

Uma aplicação de chat multithreaded em Python com suporte a mensagens privadas, grupos e transferência de arquivos.

## Características

- **Mensagens Privadas**: Envie mensagens diretas para outros usuários
- **Grupos**: Crie e participe de grupos de conversa
- **Transferência de Arquivos**: Compartilhe arquivos com usuários ou grupos
- **Entrega Offline**: Mensagens são armazenadas e entregues quando o usuário se conecta
- **Interface CLI**: Interface de linha de comando simples e intuitiva

## Estrutura do Projeto

```
chat-app/
├── server.py          # Servidor multithreaded
├── client.py          # Cliente CLI
├── downloads/         # Pasta para arquivos recebidos (criada automaticamente)
└── server_storage/    # Armazenamento temporário do servidor (criada automaticamente)
```

## Requisitos

- Python 3.6+
- Bibliotecas padrão do Python (socket, threading, json, etc.)

## Instalação e Uso

### 1. Executando o Servidor

```bash
python server.py --host 0.0.0.0 --port 9009
```

**Parâmetros:**
- `--host`: Endereço IP do servidor (padrão: `0.0.0.0`)
- `--port`: Porta do servidor (padrão: `9009`)

### 2. Conectando um Cliente

```bash
python client.py --host localhost --port 9009 --username seu_usuario
```

**Parâmetros:**
- `--host`: Endereço do servidor (obrigatório)
- `--port`: Porta do servidor (padrão: `9009`)
- `--username`: Nome de usuário (obrigatório)

## Comandos do Cliente

### Mensagens Privadas
```
/msg <usuário> <mensagem>
```
Envia uma mensagem privada para um usuário específico.

### Grupos
```
/group create <nome_do_grupo>    # Cria um novo grupo
/group join <nome_do_grupo>      # Entra em um grupo existente
/group msg <nome_do_grupo> <mensagem>  # Envia mensagem para o grupo
/group add <nome_do_grupo> <usuário>   # Adiciona usuário ao grupo
```

### Transferência de Arquivos
```
/sendfile <usuário> <caminho_do_arquivo>        # Envia arquivo para usuário
/sendgroupfile <grupo> <caminho_do_arquivo>     # Envia arquivo para grupo
```

### Utilitários
```
/list     # Lista usuários online e grupos
/clear    # Limpa a tela
/quit     # Sai do chat
```

## Exemplo de Uso

### Terminal 1 - Servidor
```bash
$ python server.py --host localhost --port 9009
Servidor ouvindo em localhost:9009
```

### Terminal 2 - Cliente Alice
```bash
$ python client.py --host localhost --port 9009 --username alice
Autenticado com sucesso.
> /group create estudos
[INFO] group_created:estudos
> /group msg estudos Olá pessoal!
```

### Terminal 3 - Cliente Bob
```bash
$ python client.py --host localhost --port 9009 --username bob
Autenticado com sucesso.
> /group join estudos
[INFO] joined:estudos
> /msg alice Oi Alice, como vai?
```

## Funcionalidades Avançadas

### Entrega Offline
- Mensagens enviadas para usuários offline são armazenadas no servidor
- Quando o usuário se conecta, recebe todas as mensagens pendentes
- Arquivos são temporariamente salvos em disco para entrega posterior

### Gerenciamento de Grupos
- Apenas membros do grupo podem adicionar novos usuários
- Mensagens de grupo são entregues a todos os membros (exceto o remetente)
- Estrutura flexível permite múltiplos grupos por usuário

### Transferência de Arquivos
- Suporte a arquivos de qualquer tipo e tamanho
- Arquivos recebidos são salvos na pasta `downloads/`
- Nomenclatura automática para evitar conflitos: `{remetente}_{nome_arquivo}`

## Protocolo de Comunicação

A aplicação utiliza um protocolo JSON sobre TCP:
1. **Header**: 4 bytes indicando o tamanho da mensagem
2. **Payload**: Dados JSON com as informações da mensagem
3. **File Data**: Dados binários do arquivo (quando aplicável)

### Tipos de Mensagem
- `auth`: Autenticação inicial
- `msg`: Mensagem privada
- `group_msg`: Mensagem de grupo
- `file`: Transferência de arquivo
- `create_group`: Criação de grupo
- `join_group`: Entrada em grupo
- `add_to_group`: Adicionar usuário ao grupo
- `list`: Listar usuários e grupos
- `quit`: Desconexão

