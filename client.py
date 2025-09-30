#!/usr/bin/env python3
"""
client_async.py
Cliente CLI assíncrono para o chat.
Uso: python client_async.py --host HOST --port PORT --username USERNAME

Comandos:
  /msg <user> <message>
  /group create <group>
  /group join <group>
  /group add <group> <user>
  /group msg <group> <message>
  /sendfile <user> <path>
  /sendgroupfile <group> <path>
  /list
  /quit
"""
import argparse
import asyncio
import json
import os
import struct
from typing import Optional

DOWNLOADS = "downloads"
os.makedirs(DOWNLOADS, exist_ok=True)


async def send_header(writer: asyncio.StreamWriter, header: dict):
    """Envia um header JSON precedido pelo tamanho."""
    data = json.dumps(header).encode("utf-8")
    writer.write(struct.pack(">I", len(data)) + data)
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


async def listener(reader: asyncio.StreamReader):
    """Escuta mensagens do servidor em loop assíncrono."""
    while True:
        try:
            header = await recv_header(reader)
            if header is None:
                print("\n[SISTEMA] Conexão perdida com servidor.")
                break
            
            msg_type = header.get("type")
            
            if msg_type == "info":
                print(f"\n[INFO] {header.get('message')}")
            
            elif msg_type == "error":
                print(f"\n[ERRO] {header.get('message')}")
            
            elif msg_type in ("msg", "deliver_msg"):
                print(f"\n[{header.get('from')} -> você] {header.get('text')}")
            
            elif msg_type == "group_msg":
                print(f"\n[{header.get('group')}][{header.get('from')}] {header.get('text')}")
            
            elif msg_type == "list":
                print(f"\n[ONLINE] {header.get('online')}")
                print(f"[GRUPOS] {header.get('groups')}")
            
            elif msg_type in ("file", "deliver_file"):
                fname = header.get("filename")
                fsize = header.get("filesize", 0)
                sender = header.get("from")
                
                data = await reader.readexactly(fsize) if fsize > 0 else b""
                
                save_name = os.path.join(DOWNLOADS, f"{sender}_{fname}")
                
                # Escrever arquivo em thread pool para não bloquear
                loop = asyncio.get_event_loop()
                await loop.run_in_executor(None, _write_file, save_name, data)
                
                if header.get("target") == "group":
                    print(f"\n[ARQUIVO][{header.get('group')}][{sender}] recebido: {save_name} ({fsize} bytes)")
                else:
                    print(f"\n[ARQUIVO][{sender}] recebido: {save_name} ({fsize} bytes)")
            
            else:
                print(f"\n[DEBUG] header: {header}")
            
            # Reexibir prompt
            print("> ", end="", flush=True)
        
        except Exception as e:
            print(f"\n[ERRO] Listener: {e}")
            break


def _write_file(path: str, data: bytes):
    """Helper síncrono para escrita de arquivo."""
    with open(path, "wb") as f:
        f.write(data)


def _read_file(path: str) -> bytes:
    """Helper síncrono para leitura de arquivo."""
    with open(path, "rb") as f:
        return f.read()


async def send_file(writer: asyncio.StreamWriter, header: dict, path: str):
    """Envia arquivo para servidor."""
    await send_header(writer, header)
    
    # Ler arquivo em chunks via thread pool
    loop = asyncio.get_event_loop()
    data = await loop.run_in_executor(None, _read_file, path)
    
    # Enviar em chunks
    chunk_size = 4096
    for i in range(0, len(data), chunk_size):
        chunk = data[i:i+chunk_size]
        writer.write(chunk)
        await writer.drain()


async def interactive(reader: asyncio.StreamReader, writer: asyncio.StreamWriter, username: str):
    """Loop interativo assíncrono para comandos do usuário."""
    loop = asyncio.get_event_loop()
    
    while True:
        try:
            # Ler input do usuário em thread pool para não bloquear event loop
            cmd = await loop.run_in_executor(None, input, "> ")
            cmd = cmd.strip()
        except EOFError:
            cmd = "/quit"
        
        if not cmd:
            continue
        
        parts = cmd.split()
        
        try:
            if parts[0] == "/msg" and len(parts) >= 3:
                to = parts[1]
                text = " ".join(parts[2:])
                await send_header(writer, {"type": "msg", "to": to, "text": text})
            
            elif parts[0] == "/group" and len(parts) >= 2:
                sub = parts[1]
                
                if sub == "create" and len(parts) >= 3:
                    await send_header(writer, {"type": "create_group", "group": parts[2]})
                
                elif sub == "join" and len(parts) >= 3:
                    await send_header(writer, {"type": "join_group", "group": parts[2]})
                
                elif sub == "msg" and len(parts) >= 4:
                    group_name = parts[2]
                    text = " ".join(parts[3:])
                    await send_header(writer, {"type": "group_msg", "group": group_name, "text": text})
                
                elif sub == "add" and len(parts) >= 4:
                    group_name = parts[2]
                    user_to_add = parts[3]
                    await send_header(writer, {
                        "type": "add_to_group",
                        "group": group_name,
                        "user_to_add": user_to_add
                    })
                
                else:
                    print("Uso: /group create|join|msg|add...")
            
            elif parts[0] == "/sendfile" and len(parts) >= 3:
                to = parts[1]
                path = " ".join(parts[2:])
                
                if not os.path.exists(path):
                    print("Arquivo não encontrado")
                    continue
                
                filesize = os.path.getsize(path)
                header = {
                    "type": "file",
                    "to": to,
                    "filename": os.path.basename(path),
                    "filesize": filesize,
                    "target": "user"
                }
                
                await send_file(writer, header, path)
                print("Arquivo enviado.")
            
            elif parts[0] == "/sendgroupfile" and len(parts) >= 3:
                group = parts[1]
                path = " ".join(parts[2:])
                
                if not os.path.exists(path):
                    print("Arquivo não encontrado")
                    continue
                
                filesize = os.path.getsize(path)
                header = {
                    "type": "file",
                    "group": group,
                    "filename": os.path.basename(path),
                    "filesize": filesize,
                    "target": "group"
                }
                
                await send_file(writer, header, path)
                print("Arquivo de grupo enviado.")
            
            elif parts[0] == "/list":
                await send_header(writer, {"type": "list"})
            
            elif parts[0] == "/clear":
                os.system("cls" if os.name == "nt" else "clear")
            
            elif parts[0] == "/quit":
                await send_header(writer, {"type": "quit"})
                print("Saindo...")
                writer.close()
                await writer.wait_closed()
                break
            
            else:
                print("Comandos: /msg, /group, /sendfile, /sendgroupfile, /list, /clear, /quit")
        
        except Exception as e:
            print(f"Erro ao processar comando: {e}")


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", required=True)
    parser.add_argument("--port", type=int, default=9009)
    parser.add_argument("--username", required=True)
    args = parser.parse_args()
    
    try:
        # Conectar ao servidor
        reader, writer = await asyncio.open_connection(args.host, args.port)
        
        # Autenticar
        await send_header(writer, {"type": "auth", "username": args.username})
        hdr = await recv_header(reader)
        
        if hdr is None or hdr.get("type") == "error":
            print(f"Falha ao autenticar: {hdr}")
            writer.close()
            await writer.wait_closed()
            return
        
        print("Autenticado com sucesso.")
        print("Digite /help para ver os comandos disponíveis.\n")
        
        # Iniciar listener em background
        listener_task = asyncio.create_task(listener(reader))
        
        # Loop interativo
        try:
            await interactive(reader, writer, args.username)
        finally:
            listener_task.cancel()
            try:
                await listener_task
            except asyncio.CancelledError:
                pass
    
    except ConnectionRefusedError:
        print(f"Erro: Não foi possível conectar ao servidor {args.host}:{args.port}")
    except Exception as e:
        print(f"Erro: {e}")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nInterrompido pelo usuário.")
