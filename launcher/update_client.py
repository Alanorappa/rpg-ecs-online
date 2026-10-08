"""
launcher/update_client.py
Lógica PURA do Launcher (08/10/2026, ver CLAUDE.md) — consulta versão,
baixa, confere integridade, extrai. Zero tkinter aqui de propósito (só
`launcher/login_ui.py` sabe desenhar janela) — mantém esta camada
testável sem precisar abrir UI nenhuma, mesma disciplina de separação
já usada no projeto (engine/ vs ui/, server/ vs client/).

Só stdlib (urllib/ssl/hashlib/zipfile/subprocess) — zero dependência
nova pro Launcher, só o jogo/servidor ganham aiohttp (server/http_api.py).
"""
from __future__ import annotations

import hashlib
import json
import os
import ssl
import subprocess
import urllib.request
import urllib.error

VERSION_FILE = "version.txt"
GAME_EXE     = "RPG_Online.exe"
DOWNLOAD_CHUNK = 65536


def build_ssl_context(cert_path: "str | None") -> "ssl.SSLContext | None":
    """Mesmo padrão de pin de certificado autoassinado que
    client/network.py::_build_client_ssl_context já usa — None = sem
    TLS (dev local), cafile= = certificado fixado (produção)."""
    if not cert_path:
        return None
    return ssl.create_default_context(cafile=cert_path)


def get_local_version(install_dir: str) -> str:
    """"" (string vazia) se o arquivo não existir — 1ª instalação feita
    manualmente (zip da itch.io, sem version.txt ainda) nunca derruba o
    launcher, só trata como "sempre desatualizado" (força 1 update)."""
    path = os.path.join(install_dir, VERSION_FILE)
    if not os.path.isfile(path):
        return ""
    with open(path, "r", encoding="utf-8") as f:
        return f.read().strip()


def fetch_version_manifest(base_url: str, ssl_context) -> dict:
    """GET /launcher/version -> {"version": ..., "sha256": ...}."""
    req = urllib.request.Request(f"{base_url}/launcher/version")
    with urllib.request.urlopen(req, context=ssl_context, timeout=10) as resp:
        return json.loads(resp.read().decode("utf-8"))


def check_login(base_url: str, username: str, password_hash: str, ssl_context) -> bool:
    """POST /launcher/login — gate de conveniência, NUNCA abre sessão de
    jogo (isso acontece de novo quando RPG_Online.exe sobe, via WS)."""
    body = json.dumps({"username": username, "password_hash": password_hash}).encode("utf-8")
    req = urllib.request.Request(
        f"{base_url}/launcher/login", data=body,
        headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, context=ssl_context, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        data = json.loads(e.read().decode("utf-8"))
    return bool(data.get("ok"))


def download_release(base_url: str, dest_path: str, ssl_context,
                     progress_cb=None) -> None:
    """Baixa /launcher/download pra `dest_path` (arquivo temporário —
    chamador decide onde; nunca escreve direto em cima do zip final até
    confirmar sha256, ver `verify_sha256`)."""
    req = urllib.request.Request(f"{base_url}/launcher/download")
    with urllib.request.urlopen(req, context=ssl_context, timeout=30) as resp:
        total = int(resp.headers.get("Content-Length", 0))
        written = 0
        with open(dest_path, "wb") as f:
            while True:
                chunk = resp.read(DOWNLOAD_CHUNK)
                if not chunk:
                    break
                f.write(chunk)
                written += len(chunk)
                if progress_cb and total:
                    progress_cb(written, total)


def verify_sha256(file_path: str, expected: str) -> bool:
    """"" (hash esperado vazio) NUNCA passa — sem hash publicado, não
    aplica a atualização (ver server/http_api.py — sha256 vazio
    acontece só antes do 1º release_client.ps1 rodar). O early-return
    é só documentação explícita do comportamento — um hexdigest de 64
    chars nunca bate com "" de qualquer forma, mas deixar implícito
    dependeria de ninguém mudar o formato do hash sem perceber."""
    if not expected:
        return False
    h = hashlib.sha256()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(DOWNLOAD_CHUNK), b""):
            h.update(chunk)
    return h.hexdigest() == expected


def apply_update(zip_path: str, install_dir: str) -> None:
    """Extrai por cima da pasta de instalação. Seguro porque o zip
    publicado por release_tools/release_client.ps1 NUNCA contém
    Launcher.exe (só os arquivos do jogo) — o processo do launcher
    nunca precisa se autossubstituir, elimina o problema de arquivo
    travado do Windows por completo."""
    import zipfile
    with zipfile.ZipFile(zip_path, "r") as zf:
        zf.extractall(install_dir)


def launch_game(install_dir: str, username: str = "", password_hash: str = "") -> None:
    """Sobe RPG_Online.exe com a env var que main.py::__main__ exige (ver
    CLAUDE.md) e retorna na hora — NUNCA espera o jogo fechar.

    `username`/`password_hash` (08/10/2026, ver CLAUDE.md — bug real
    relatado pelo usuário: logar no Launcher e ter que digitar
    usuário/senha DE NOVO no jogo) — repassados por env var
    (RPG_PRELOGIN_USER/RPG_PRELOGIN_HASH), lidos por main.py pra pular a
    tela de login automaticamente. SÓ o hash, nunca a senha crua (o
    Launcher nunca guarda/repassa a senha de verdade pra lugar nenhum)."""
    exe_path = os.path.join(install_dir, GAME_EXE)
    env = dict(os.environ)
    env["RPG_LAUNCHED_BY_LAUNCHER"] = "1"
    if username and password_hash:
        env["RPG_PRELOGIN_USER"] = username
        env["RPG_PRELOGIN_HASH"] = password_hash
    subprocess.Popen([exe_path], cwd=install_dir, env=env)
