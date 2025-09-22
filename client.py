#!/usr/bin/env python3
"""
client.py
Cliente CLI para o chat.
Uso: python client.py --host HOST --port PORT --username USERNAME
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
import json
import os
import socket
import struct
import sys
import threading

DOWNLOADS = "downloads"
os.makedirs(DOWNLOADS, exist_ok=True)


def send_header(sock, header: dict):
    data = json.dumps(header).encode("utf-8")
    sock.sendall(struct.pack(">I", len(data)) + data)


def recvall(sock, n):
    buf = b""
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            return None
        buf += chunk
    return buf


def recv_header(sock):
    raw = recvall(sock, 4)
    if not raw:
        return None
    (n,) = struct.unpack(">I", raw)
    data = recvall(sock, n)
    if not data:
        return None
    return json.loads(data.decode("utf-8"))


def listener(sock):
    while True:
        try:
            header = recv_header(sock)
            if header is None:
                print("Conexão perdida com servidor.")
                break
            t = header.get("type")
            if t == "info":
                print("[INFO]", header.get("message"))
            elif t == "error":
                print("[ERRO]", header.get("message"))
            elif t == "msg" or t == "deliver_msg":
                print(f"[{header.get('from')} -> você] {header.get('text')}")
            elif t == "group_msg":
                print(
                    f"[{header.get('group')}][{header.get('from')}] {header.get('text')}"
                )
            elif t == "list":
                print("Online:", header.get("online"))
                print("Groups:", header.get("groups"))
            elif t == "file" or t == "deliver_file":
                fname = header.get("filename")
                fsize = header.get("filesize", 0)
                sender = header.get("from")
                group = header.get("group")
                data = recvall(sock, fsize) if fsize > 0 else b""
                save_name = os.path.join(DOWNLOADS, f"{sender}_{fname}")
                with open(save_name, "wb") as f:
                    f.write(data)
                if header.get("target") == "group":
                    print(
                        f"[ARQ][{header.get('group')}][{sender}] recebido arquivo: {save_name} ({fsize} bytes)"
                    )
                else:
                    print(
                        f"[ARQ][{sender}] recebido arquivo: {save_name} ({fsize} bytes)"
                    )
            else:
                print("[DEBUG] header:", header)
        except Exception as e:
            print("Erro listener:", e)
            break


def interactive(sock, username):
    while True:
        try:
            cmd = input("> ").strip()
        except EOFError:
            cmd = "/quit"
        if not cmd:
            continue
        parts = cmd.split()
        if parts[0] == "/msg" and len(parts) >= 3:
            to = parts[1]
            text = " ".join(parts[2:])
            send_header(sock, {"type": "msg", "to": to, "text": text})

        elif parts[0] == "/group" and len(parts) >= 2:
            sub = parts[1]
            if sub == "create" and len(parts) >= 3:
                send_header(sock, {"type": "create_group", "group": parts[2]})
            elif sub == "join" and len(parts) >= 3:
                send_header(sock, {"type": "join_group", "group": parts[2]})
            elif sub == "msg" and len(parts) >= 4:
                g = parts[2]
                text = " ".join(parts[3:])
                send_header(sock, {"type": "group_msg", "group": g, "text": text})
            elif sub == "add" and len(parts) >= 4:
                group_name = parts[2]
                user_to_add = parts[3]
                send_header(
                    sock,
                    {
                        "type": "add_to_group",
                        "group": group_name,
                        "user_to_add": user_to_add,
                    },
                )
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
                "target": "user",
            }
            send_header(sock, header)
            with open(path, "rb") as f:
                while True:
                    chunk = f.read(4096)
                    if not chunk:
                        break
                    sock.sendall(chunk)
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
                "target": "group",
            }
            send_header(sock, header)
            with open(path, "rb") as f:
                while True:
                    chunk = f.read(4096)
                    if not chunk:
                        break
                    sock.sendall(chunk)
            print("Arquivo de grupo enviado.")
        elif parts[0] == "/list":
            send_header(sock, {"type": "list"})
        elif parts[0] == "/clear":
            os.system("cls" if os.name == "nt" else "clear")
        elif parts[0] == "/quit":
            send_header(sock, {"type": "quit"})
            print("Saindo...")
            sock.close()
            break
        else:
            print(
                "Comandos: /msg <user> <text>, /group create/join/add/msg, /sendfile <user> <path>, /sendgroupfile <group> <path>, /list, /clear, /quit"
            )

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", required=True)
    parser.add_argument("--port", type=int, default=9009)
    parser.add_argument("--username", required=True)
    args = parser.parse_args()

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    
    try:
        sock.connect((args.host, args.port))
    except (socket.error, ConnectionRefusedError) as e:
        # Captura erros de socket ou quando a conexão é recusada
        print(f"Erro ao conectar ao servidor {args.host}:{args.port} - {e}")
        sock.close()
        return
    
    # Se a conexão foi bem-sucedida, continuar com a autenticação
    try:
        # auth
        send_header(sock, {"type": "auth", "username": args.username})
        hdr = recv_header(sock)
        if hdr is None or hdr.get("type") == "error":
            print("Falha ao autenticar:", hdr)
            sock.close()
            return
        print("Autenticado com sucesso.")

        # Iniciar thread de escuta (listener)
        thr = threading.Thread(target=listener, args=(sock,), daemon=True)
        thr.start()

        # Interação após autenticação
        interactive(sock, args.username)

    except Exception as e:
        # Captura erro no processo de autenticação ou interação
        print(f"Ocorreu um erro: {e}")
        sock.close()

if __name__ == "__main__":
    main()