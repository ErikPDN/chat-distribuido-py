#!/usr/bin/env python3
"""
server.py
Servidor assíncrono para chat usando asyncio (resolve C10k problem).
fila de mensagens e persistência para usuários offline.

Uso: python server.py [--host HOST] [--port PORT]
"""
import argparse
import asyncio
import json
import os 
import struct 
from collections import defaultdict
from typing import Optional, Tuple


STORAGE_DIR = "server_storage"
os.makedirs(STORAGE_DIR, exist_ok=True)

# Estado do servidor (não precisa de locks com asyncio em single-thread)
clients = {} # username -> (StreamWriter, address)
undelivered = defaultdict(list)  # username -> list of pending (header, file_bytes)
groups = defaultdict(set)  # group_name -> set(usernames)

# Fila assíncrona para processamento de mensagens
message_queue = asyncio.Queue()

async def send_header(writer: asyncio.StreamWriter, header: dict):
    """Envia um header JSON precedido pelo tamanho."""
    data = json.dumps(header).encode("utf-8")
    header_len = struct.pack(">I", len(data))
    writer.write(header_len + data)
    await writer.drain()


async def recv_header(reader: asyncio.StreamReader) -> Optional[dict]:
    """Recebe um header JSON precedido pelo tamanho."""
    try:
        raw = await reader.readexactly(4)
        (n,) = struct.unpack(">I", raw)
        data = await reader.readexactly(n)
        return json.loads(data.decode("utf-8"))
    except (asyncio.IncompleteReadError, ConnectionError, Exception):
        return None

async def deliver_to_user(username: str, header: dict, file_bytes: Optional[bytes] = None):
    """Entrega mensagem/arquivo para um usuário online ou armazena para entrega posterior."""
    entry = clients.get(username)
    
    if entry:
        writer, _ = entry
        try:
            await send_header(writer, header)
            if file_bytes:
                writer.write(file_bytes)
                await writer.drain()
        except Exception as e:
            print(f"Erro ao entregar para {username}: {e}")
            await store_undelivered(username, header, file_bytes)
    else:
        await store_undelivered(username, header, file_bytes)

async def store_undelivered(username: str, header: dict, file_bytes: Optional[bytes]):
    """Armazena mensagens/arquivos não entregues."""
    if header.get("type") == "file" and file_bytes is not None:
        # Persistir arquivo em disco
        file_name = header.get("filename", "unknown")
        uid = f"{username}_{asyncio.current_task().get_name()}_{file_name}"
        path = os.path.join(STORAGE_DIR, uid)
        
        # Operação de I/O em thread pool para não bloquear event loop
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, _write_file, path, file_bytes)
        
        new_header = dict(header)
        new_header["offline_path"] = path
        undelivered[username].append((new_header, None))
    else:
        undelivered[username].append((header, file_bytes))

def _write_file(path: str, data: bytes):
    """Helper síncrono para escrita de arquivo."""
    with open(path, "wb") as f:
        f.write(data)

def _read_file(path: str) -> bytes:
    """Helper síncrono para leitura de arquivo."""
    with open(path, "rb") as f:
        return f.read()

async def handle_undelivered(username: str):
    """Entrega mensagens pendentes quando usuário se conecta."""
    items = undelivered.get(username, [])
    if not items:
        return

    writer, _ = clients.get(username)
    if not writer:
        return

    loop = asyncio.get_event_loop()
    for header, file_bytes in items:
        try:
            if header.get("offline_path"):
                path = header["offline_path"]
                # Ler arquivo em thread pool
                buffer = await loop.run_in_executor(None, _read_file, path)

                header2 = dict(header)
                header2.pop("offline_path", None)
                header2["filesize"] = len(buffer)

                await send_header(writer, header2)
                writer.write(buffer)
                await writer.drain()

                # Remover arquivo após entrega
                await loop.run_in_executor(None, os.remove, path)
            else:
                await send_header(writer, header)
                if file_bytes:
                    writer.write(file_bytes)
                    await writer.drain()
        except Exception as e:
            print(f"Erro ao entregar mensagem offline: {e}")

    # Limpar fila
    undelivered.pop(username, None)

async def broadcast_group(group_name: str, header: dict, file_bytes: Optional[bytes] = None):
    """Envia mensagem para todos membros de um grupo (exceto remetente)."""
    members = list(groups.get(group_name, []))
    sender = header.get("from")
    
    # Criar tasks para entrega paralela
    tasks = []
    for member in members:
        if member != sender:
            tasks.append(deliver_to_user(member, header, file_bytes))
    
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)

async def message_dispatcher():
    """
    Processa mensagens da fila assíncrona e as distribui.
    Roda como uma corrotina separada.
    """
    while True:
        header, file_bytes, origin_username, target = await message_queue.get()
        
        try:
            if target.get("type") == "group":
                group_name = target.get("name")
                header["from"] = origin_username
                await broadcast_group(group_name, header, file_bytes)
            
            elif target.get("type") == "user":
                username = target.get("name")
                header["from"] = origin_username
                await deliver_to_user(username, header, file_bytes)
        
        except Exception as e:
            print(f"Erro no dispatcher: {e}")
        
        finally:
            message_queue.task_done()

async def handle_client(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
    """Gerencia conexão de um cliente."""
    addr = writer.get_extra_info('peername')
    print(f"Conexão de {addr}")
    username = None
    
    try:
        # Autenticação
        header = await recv_header(reader)
        if not header or header.get("type") != "auth" or "username" not in header:
            await send_header(writer, {"type": "error", "message": "auth required"})
            writer.close()
            await writer.wait_closed()
            return
        
        username = header["username"]
        
        if username in clients:
            await send_header(writer, {"type": "error", "message": "username_taken"})
            writer.close()
            await writer.wait_closed()
            return
        
        clients[username] = (writer, addr)
        await send_header(writer, {"type": "info", "message": "auth_ok"})
        print(f"Usuário autenticado: {username} de {addr}")
        
        # Entregar mensagens pendentes
        await handle_undelivered(username)
        
        # Loop de recebimento
        while True:
            header = await recv_header(reader)
            if header is None:
                print(f"{username} desconectou")
                break
            
            msg_type = header.get("type")
            
            if msg_type == "msg":
                to = header.get("to")
                target = {"type": "user", "name": to}
                await message_queue.put((header, None, username, target))
            
            elif msg_type == "group_msg":
                g_name = header.get("group")
                target = {"type": "group", "name": g_name}
                await message_queue.put((header, None, username, target))
            
            elif msg_type == "create_group":
                g_name = header.get("group")
                groups[g_name].add(username)
                await send_header(writer, {"type": "info", "message": f"group_created:{g_name}"})
            
            elif msg_type == "join_group":
                g_name = header.get("group")
                groups[g_name].add(username)
                await send_header(writer, {"type": "info", "message": f"joined:{g_name}"})
            
            elif msg_type == "add_to_group":
                g_name = header.get("group")
                user_to_add = header.get("user_to_add")
                
                if g_name not in groups or username not in groups[g_name]:
                    await send_header(writer, {
                        "type": "error",
                        "message": f"Você não tem permissão para adicionar usuários ao grupo '{g_name}'."
                    })
                    continue
                
                groups[g_name].add(user_to_add)
                await send_header(writer, {
                    "type": "info",
                    "message": f"Usuário '{user_to_add}' foi adicionado ao grupo '{g_name}'."
                })
                
                notification_header = {
                    "type": "info",
                    "message": f"Você foi adicionado ao grupo '{g_name}' por {username}."
                }
                target = {"type": "user", "name": user_to_add}
                await message_queue.put((notification_header, None, username, target))
            
            elif msg_type == "list":
                online = list(clients.keys())
                await send_header(writer, {
                    "type": "list",
                    "online": online,
                    "groups": {g_name: list(m) for g_name, m in groups.items()}
                })
            
            elif msg_type == "file":
                filesize = header.get("filesize", 0)
                file_bytes = await reader.readexactly(filesize) if filesize > 0 else b""
                
                if header.get("target") == "group":
                    g_name = header.get("group")
                    target = {"type": "group", "name": g_name}
                    await message_queue.put((header, file_bytes, username, target))
                else:
                    to = header.get("to")
                    target = {"type": "user", "name": to}
                    await message_queue.put((header, file_bytes, username, target))
            
            elif msg_type == "quit":
                print(f"{username} desconectou")
                break
            
            else:
                await send_header(writer, {"type": "error", "message": "unknown_type"})
    
    except Exception as e:
        print(f"Exceção ao lidar com cliente: {e}")
    
    finally:
        # Cleanup
        if username:
            clients.pop(username, None)
        writer.close()
        await writer.wait_closed()
        print(f"Conexão finalizada: {addr}")

async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=9009)
    args = parser.parse_args()
    
    # Iniciar dispatcher em background
    asyncio.create_task(message_dispatcher())
    
    # Iniciar servidor
    server = await asyncio.start_server(
        handle_client,
        args.host,
        args.port
    )
    
    addr = server.sockets[0].getsockname()
    print(f"Servidor ouvindo em {args.host}:{args.port}")
    
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    asyncio.run(main())
