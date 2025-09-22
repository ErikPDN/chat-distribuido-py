#!/usr/bin/env python3
"""
server.py
Servidor multithreaded para chat (mensagens privadas, grupos, transferência de arquivos).
Uso: python server.py [HOST] [PORT]
"""
import argparse
import json
import os
import queue
import socket
import struct
import threading
from collections import defaultdict

STORAGE_DIR = "server_storage"
os.makedirs(STORAGE_DIR, exist_ok=True)

# Estado do servidor
clients_lock = threading.Lock()
clients = {}  # username -> (socket, addr)
undelivered = defaultdict(
    list
)  # username -> list of pending headers+payload (for offline delivery)
groups = defaultdict(set)  # group_name -> set(usernames)
message_queue = queue.Queue()


def send_header(sock, header: dict):
    data = json.dumps(header).encode("utf-8")
    header_len = struct.pack(">I", len(data))
    sock.sendall(header_len + data)


def recv_header(sock):
    # lê 4 bytes do tamanho
    raw = recv_all(sock, 4)
    if not raw:
        return None
    (n,) = struct.unpack(">I", raw)
    data = recv_all(sock, n)
    if not data:
        return None
    return json.loads(data.decode("utf-8"))


def recv_all(sock, n):
    buf = b""
    while len(buf) < n:
        try:
            chunk = sock.recv(n - len(buf))
        except:
            return None
        if not chunk:
            return None
        buf += chunk
    return buf


def deliver_to_user(username, header, file_bytes=None):
    with clients_lock:
        entry = clients.get(username)
    if entry:
        sock, _ = entry
        try:
            send_header(sock, header)
            if file_bytes:
                sock.sendall(file_bytes)
        except Exception as e:
            print(f"Erro ao entregar para {username}: {e}")
            store_undelivered(username, header, file_bytes)
    else:
        store_undelivered(username, header, file_bytes)


def store_undelivered(username, header, file_bytes):
    # Simples: armazenar em memória; para arquivos, persistir em disco
    if header.get("type") == "file" and file_bytes is not None:
        # salvar no storage e mudar header para apontar para arquivo salvo
        fname = header.get("filename")
        uid = f"{username}_{threading.get_ident()}_{fname}"
        path = os.path.join(STORAGE_DIR, uid)
        with open(path, "wb") as f:
            f.write(file_bytes)
        new_header = dict(header)
        new_header["offline_path"] = path
        undelivered[username].append((new_header, None))
    else:
        undelivered[username].append((header, file_bytes))


def handle_undelivered(username):
    items = []
    # puxar fila
    items = undelivered.get(username, [])
    if not items:
        return
    with clients_lock:
        sock, _ = clients.get(username)
    for header, file_bytes in items:
        # se header tiver offline_path, ler o arquivo
        if header.get("offline_path"):
            path = header["offline_path"]
            try:
                with open(path, "rb") as f:
                    b = f.read()
                header2 = dict(header)
                header2.pop("offline_path", None)
                header2["filesize"] = len(b)
                send_header(sock, header2)
                sock.sendall(b)
                os.remove(path)
            except Exception as e:
                print("Erro entrega offline arquivo:", e)
        else:
            try:
                send_header(sock, header)
                if file_bytes:
                    sock.sendall(file_bytes)
            except Exception as e:
                print("Erro entrega offline:", e)
    # limpar fila
    undelivered.pop(username, None)


def broadcast_group(group_name, header, file_bytes=None):
    members = list(groups.get(group_name, []))
    for u in members:
        if u == header.get("from"):
            continue
        deliver_to_user(u, header, file_bytes)


def message_dispatcher():
    """
    Esta função roda em uma thread separada.
    Ela consome mensagens da fila e as distribui.
    """
    while True:
        # get() é bloqueante, a thread vai dormir até uma mensagem chegar
        header, file_bytes, origin_username, target = message_queue.get()

        if target.get("type") == "group":
            group_name = target.get("name")
            # Adiciona o remetente ao header para que o broadcast possa ignorá-lo
            header["from"] = origin_username
            broadcast_group(group_name, header, file_bytes)

        elif target.get("type") == "user":
            username = target.get("name")
            header["from"] = origin_username
            deliver_to_user(username, header, file_bytes)


def client_thread(conn, addr):
    print(f"Conexão de {addr}")
    username = None
    try:
        # primeiro passo: autenticação
        header = recv_header(conn)
        if not header or header.get("type") != "auth" or "username" not in header:
            send_header(conn, {"type": "error", "message": "auth required"})
            conn.close()
            return
        username = header["username"]
        with clients_lock:
            if username in clients:
                send_header(conn, {"type": "error", "message": "username_taken"})
                conn.close()
                return
            clients[username] = (conn, addr)
        send_header(conn, {"type": "info", "message": "auth_ok"})
        print(f"Usuário autenticado: {username} de {addr}")

        # entregar mensagens pendentes
        handle_undelivered(username)

        # loop de recebimento
        while True:
            header = recv_header(conn)
            if header is None:
                print(f"{username} desconectou")
                break
            t = header.get("type")
            if t == "msg":
                to = header.get("to")
                target = {"type": "user", "name": to}
                message_queue.put((header, None, username, target))
            elif t == "group_msg":
                g_name = header.get("group")
                target = {"type": "group", "name": g_name}
                message_queue.put((header, None, username, target))
            elif t == "create_group":
                g_name = header.get("group")
                groups[g_name].add(username)
                send_header(conn, {"type": "info", "message": f"group_created:{g_name}"})
            elif t == "join_group":
                g_name = header.get("group")
                groups[g_name].add(username)
                send_header(conn, {"type": "info", "message": f"joined:{g_name}"})
            elif t == "add_to_group":
                g_name = header.get("group")
                user_to_add = header.get("user_to_add")

                if g_name not in groups or username not in groups[g_name]:
                    send_header(
                        conn,
                        {
                            "type": "error",
                            "message": f"Você não tem permissão para adicionar usuários ao grupo '{g_name}'.",
                        },
                    )
                    continue

                groups[g_name].add(user_to_add)
                send_header(
                    conn,
                    {
                        "type": "info",
                        "message": f"Usuário '{user_to_add}' foi adicionado ao grupo '{g_name}'.",
                    },
                )
                notification_header = {
                    "type": "info",
                    "message": f"Você foi adicionado ao groupo '{g_name}' pelo {username}.",
                }
                target = {"type": "user", "name": user_to_add}
                message_queue.put((notification_header, None, username, target))
            elif t == "list":
                with clients_lock:
                    online = list(clients.keys())
                send_header(
                    conn,
                    {
                        "type": "list",
                        "online": online,
                        "groups": {g_name: list(m) for g_name, m in groups.items()},
                    },
                )
            elif t == "file":
                filesize = header.get("filesize", 0)
                file_bytes = recv_all(conn, filesize) if filesize > 0 else b""

                if header.get("target") == "group":
                    g_name = header.get("group")
                    target = {"type": "group", "name": g_name}
                    message_queue.put((header, file_bytes, username, target))
                else:
                    to = header.get("to")
                    target = {"type": "user", "name": to}
                    message_queue.put((header, file_bytes, username, target))

            elif t == "quit":
                print(f"{username} desconectou")
                break
            else:
                send_header(conn, {"type": "error", "message": "unknown_type"})
    except Exception as e:
        print("Exceção thread:", e)
    finally:
        # cleanup
        conn.close()
        if username:
            with clients_lock:
                clients.pop(username, None)
        print(f"Conexão finalizada: {addr}")


def accept_loop(server_sock):
    while True:
        conn, addr = server_sock.accept()
        thr = threading.Thread(target=client_thread, args=(conn, addr), daemon=True)
        thr.start()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=9009)
    args = parser.parse_args()
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind((args.host, args.port))
    srv.listen(100)
    print(f"Servidor ouvindo em {args.host}:{args.port}")

    dispatcher = threading.Thread(target=message_dispatcher, daemon=True)
    dispatcher.start()

    accept_loop(srv)


if __name__ == "__main__":
    main()
