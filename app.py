"""
Portal da oficina
Python + HTML. O cliente abre um link e vê a situação do veículo.
Não lê ECU e não altera arquivo de veículo.
"""

import html
import os
import socket
import sqlite3
import secrets
import threading
import webbrowser
from datetime import datetime
from pathlib import Path
from functools import wraps
from urllib.parse import quote

from flask import (
    Flask, request, redirect, url_for, session, send_from_directory,
    abort, flash, render_template_string,
)
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.utils import secure_filename

BASE = Path(__file__).resolve().parent
CONFIG_PATH = BASE / "config.txt"
NO_RAILWAY = bool(os.environ.get("RAILWAY_ENVIRONMENT") or os.environ.get("RAILWAY_PROJECT_ID"))


def read_config():
    data = {}
    if CONFIG_PATH.exists():
        texto = CONFIG_PATH.read_text(encoding="utf-8-sig")
        for line in texto.splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            chave, valor = line.split("=", 1)
            data[chave.strip().upper()] = valor.strip()
    if "SECRET" not in data and not NO_RAILWAY:
        data["SECRET"] = secrets.token_hex(24)
        try:
            linhas = [f"{k}={v}" for k, v in data.items()]
            CONFIG_PATH.write_text("\n".join(linhas) + "\n", encoding="utf-8")
        except OSError:
            pass
    return data


CFG = read_config()
OFICINA = CFG.get("NOME") or os.environ.get("OFICINA_NOME", "Minha Oficina")
SENHA = CFG.get("SENHA") or os.environ.get("OFICINA_SENHA", "troque-esta-senha")
SECRET = os.environ.get("OFICINA_SECRET") or CFG.get("SECRET") or secrets.token_hex(24)
PORTA = int(CFG.get("PORTA") or os.environ.get("OFICINA_PORTA", "5000"))
URL_PUBLICA = (CFG.get("URL_PUBLICA") or os.environ.get("OFICINA_URL", "")).rstrip("/")

DATA = Path(
    os.environ.get("DATA_DIR")
    or os.environ.get("RAILWAY_VOLUME_MOUNT_PATH")
    or BASE
)
DB_PATH = DATA / "oficina.db"
UPLOADS = DATA / "uploads"

STATUS = [
    "Recebido na oficina",
    "Em diagnóstico",
    "Aguardando aprovação",
    "Aguardando peça",
    "Em execução",
    "Pronto para retirada",
    "Entregue",
]

app = Flask(__name__)
app.secret_key = SECRET
app.config["MAX_CONTENT_LENGTH"] = 16 * 1024 * 1024
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)


def h(valor):
    return html.escape("" if valor is None else str(valor))


def url_publica():
    if URL_PUBLICA:
        return URL_PUBLICA
    dominio = os.environ.get("RAILWAY_PUBLIC_DOMAIN", "").strip().rstrip("/")
    if dominio:
        if dominio.startswith("http://") or dominio.startswith("https://"):
            return dominio
        return "https://" + dominio
    return ""


def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    DATA.mkdir(parents=True, exist_ok=True)
    UPLOADS.mkdir(parents=True, exist_ok=True)
    with db() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS ordens (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                token TEXT UNIQUE NOT NULL,
                cliente TEXT NOT NULL,
                telefone TEXT,
                placa TEXT NOT NULL,
                marca TEXT,
                modelo TEXT,
                descricao TEXT,
                status TEXT NOT NULL,
                criado_em TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS eventos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ordem_id INTEGER NOT NULL,
                status TEXT NOT NULL,
                nota TEXT,
                criado_em TEXT NOT NULL,
                FOREIGN KEY (ordem_id) REFERENCES ordens(id)
            );
            CREATE TABLE IF NOT EXISTS arquivos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ordem_id INTEGER NOT NULL,
                nome_original TEXT NOT NULL,
                arquivo TEXT NOT NULL,
                tipo TEXT NOT NULL,
                criado_em TEXT NOT NULL,
                FOREIGN KEY (ordem_id) REFERENCES ordens(id)
            );
            """
        )


def agora():
    return datetime.now().strftime("%d/%m/%Y %H:%M")


def ip_local():
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.connect(("8.8.8.8", 80))
        ip = sock.getsockname()[0]
        sock.close()
        return ip
    except OSError:
        return "127.0.0.1"


def link_cliente(token):
    publica = url_publica()
    if publica:
        return f"{publica}/c/{token}"
    host = request.host
    if host.startswith("127.0.0.1") or host.startswith("localhost"):
        return f"http://{ip_local()}:{PORTA}/c/{token}"
    esquema = request.headers.get("X-Forwarded-Proto", request.scheme) or "https"
    esquema = esquema.split(",")[0].strip()
    return f"{esquema}://{host}/c/{token}"


def login_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not session.get("admin"):
            return redirect(url_for("login"))
        return fn(*args, **kwargs)
    return wrapper


BASE_CSS = """
:root { color-scheme: dark; }
* { box-sizing: border-box; }
body {
  margin: 0; font-family: Segoe UI, Arial, sans-serif;
  background: #0b0d10; color: #f2f4f7;
}
header {
  display: flex; justify-content: space-between; align-items: center;
  padding: 16px 22px; border-bottom: 1px solid #243041; background: #10141a;
}
a { color: #7eb6ff; text-decoration: none; }
main { max-width: 980px; margin: 0 auto; padding: 22px; }
.card {
  background: #151b23; border: 1px solid #2a3544; border-radius: 14px;
  padding: 18px; margin-bottom: 16px;
}
label { display: block; font-size: 13px; color: #b7c0cc; margin: 10px 0 6px; }
input, select, textarea {
  width: 100%; padding: 11px 12px; border-radius: 10px;
  border: 1px solid #334155; background: #0f141b; color: white;
}
textarea { min-height: 90px; }
button, .btn {
  display: inline-block; margin-top: 12px; background: #2563eb; color: white;
  border: 0; border-radius: 10px; padding: 11px 16px; font-weight: 700; cursor: pointer;
}
.btn.sec { background: #1f2937; }
.grid { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
table { width: 100%; border-collapse: collapse; }
th, td { text-align: left; padding: 10px 8px; border-bottom: 1px solid #243041; }
.badge {
  display: inline-block; background: #1d4ed8; color: white;
  border-radius: 999px; padding: 6px 10px; font-size: 13px;
}
.muted { color: #93a0b0; font-size: 13px; }
.flash { background: #14532d; padding: 10px 12px; border-radius: 10px; }
.aviso { background: #78350f; padding: 10px 12px; border-radius: 10px; }
@media (max-width: 700px) { .grid { grid-template-columns: 1fr; } }
"""

LAYOUT = """
<!doctype html>
<html lang="pt-BR">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{{ titulo }} · {{ oficina }}</title>
  <style>{{ css }}</style>
</head>
<body>
  <header>
    <strong>{{ oficina }}</strong>
    <nav>
      {% if admin %}
        <a href="{{ url_for('painel') }}">Painel</a> ·
        <a href="{{ url_for('nova') }}">Nova OS</a> ·
        <a href="{{ url_for('sair') }}">Sair</a>
      {% endif %}
    </nav>
  </header>
  <main>
    {% with msgs = get_flashed_messages() %}
      {% for m in msgs %}<p class="flash">{{ m }}</p>{% endfor %}
    {% endwith %}
    {{ corpo|safe }}
  </main>
</body>
</html>
"""


def page(titulo, corpo, admin=False):
    return render_template_string(
        LAYOUT, titulo=titulo, oficina=OFICINA, css=BASE_CSS,
        admin=admin, corpo=corpo,
    )


@app.route("/login", methods=["GET", "POST"])
def login():
    aviso = ""
    if SENHA == "troque-esta-senha":
        aviso = "<p class='aviso'>A senha ainda é a padrão. No Railway, crie a variável OFICINA_SENHA. No PC, troque no config.txt.</p>"
    if request.method == "POST":
        if request.form.get("senha") == SENHA:
            session["admin"] = True
            return redirect(url_for("painel"))
        flash("Senha incorreta.")
    corpo = f"""
    <section class="card">
      <h1>Acesso da oficina</h1>
      {aviso}
      <form method="post">
        <label>Senha</label>
        <input type="password" name="senha" required>
        <button>Entrar</button>
      </form>
    </section>
    """
    return page("Login", corpo)


@app.route("/sair")
def sair():
    session.clear()
    return redirect(url_for("login"))


@app.route("/")
@login_required
def painel():
    with db() as conn:
        rows = conn.execute("SELECT * FROM ordens ORDER BY id DESC").fetchall()
    linhas = []
    for r in rows:
        linhas.append(
            f"<tr><td>{r['id']}</td><td>{h(r['placa'])}</td>"
            f"<td>{h(r['cliente'])}</td><td>{h(r['status'])}</td>"
            f"<td><a href='{url_for('ordem', ordem_id=r['id'])}'>Abrir</a></td></tr>"
        )
    publica = url_publica()
    if publica:
        aviso_rede = f"<p class='flash'>O cliente abre de qualquer internet. Os links começam com {h(publica)}</p>"
    elif NO_RAILWAY:
        aviso_rede = "<p class='aviso'>Gere um domínio em Settings, Networking, Generate Domain. Sem isso o cliente não abre o link.</p>"
    else:
        aviso_rede = "<p class='aviso'>Ainda não há endereço público. Publique no Railway ou rode Publicar na internet.bat.</p>"
    if not os.environ.get("RAILWAY_VOLUME_MOUNT_PATH") and not os.environ.get("DATA_DIR") and NO_RAILWAY:
        aviso_rede += "<p class='aviso'>Sem volume, as OS e os PDFs somem no próximo deploy. Monte um volume em /data.</p>"
    corpo = f"""
    <section class="card">
      <h1>Ordens de serviço</h1>
      {aviso_rede}
      <p class="muted">Quando você muda o status, a página do cliente atualiza sozinha.</p>
      <p><a class="btn" href="{url_for('nova')}">Nova OS</a></p>
      <table>
        <tr><th>#</th><th>Placa</th><th>Cliente</th><th>Situação</th><th></th></tr>
        {''.join(linhas) or '<tr><td colspan="5">Nenhuma OS ainda.</td></tr>'}
      </table>
    </section>
    """
    return page("Painel", corpo, admin=True)


@app.route("/os/nova", methods=["GET", "POST"])
@login_required
def nova():
    if request.method == "POST":
        token = secrets.token_urlsafe(18)
        status = STATUS[0]
        with db() as conn:
            cur = conn.execute(
                """INSERT INTO ordens
                (token, cliente, telefone, placa, marca, modelo, descricao, status, criado_em)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    token,
                    request.form["cliente"].strip(),
                    request.form.get("telefone", "").strip(),
                    request.form["placa"].strip().upper(),
                    request.form.get("marca", "").strip(),
                    request.form.get("modelo", "").strip(),
                    request.form.get("descricao", "").strip(),
                    status,
                    agora(),
                ),
            )
            conn.execute(
                "INSERT INTO eventos (ordem_id, status, nota, criado_em) VALUES (?, ?, ?, ?)",
                (cur.lastrowid, status, "Veículo recebido.", agora()),
            )
        flash("OS criada. Envie o link ao cliente.")
        return redirect(url_for("ordem", ordem_id=cur.lastrowid))
    corpo = """
    <section class="card">
      <h1>Nova ordem</h1>
      <form method="post">
        <div class="grid">
          <div><label>Cliente</label><input name="cliente" required></div>
          <div><label>Telefone (WhatsApp, só números)</label><input name="telefone" placeholder="5511999999999"></div>
          <div><label>Placa</label><input name="placa" required></div>
          <div><label>Marca</label><input name="marca"></div>
          <div><label>Modelo</label><input name="modelo"></div>
        </div>
        <label>O que será feito</label>
        <textarea name="descricao"></textarea>
        <button>Criar e gerar link</button>
      </form>
    </section>
    """
    return page("Nova OS", corpo, admin=True)


@app.route("/os/<int:ordem_id>", methods=["GET", "POST"])
@login_required
def ordem(ordem_id):
    with db() as conn:
        item = conn.execute("SELECT * FROM ordens WHERE id = ?", (ordem_id,)).fetchone()
        if not item:
            abort(404)
        if request.method == "POST":
            acao = request.form.get("acao")
            if acao == "status":
                status = request.form["status"]
                if status not in STATUS:
                    abort(400)
                nota = request.form.get("nota", "").strip()
                conn.execute("UPDATE ordens SET status = ? WHERE id = ?", (status, ordem_id))
                conn.execute(
                    "INSERT INTO eventos (ordem_id, status, nota, criado_em) VALUES (?, ?, ?, ?)",
                    (ordem_id, status, nota, agora()),
                )
                flash("Situação atualizada. O cliente já pode ver.")
            elif acao == "pdf":
                arq = request.files.get("pdf")
                if not arq or not arq.filename.lower().endswith(".pdf"):
                    flash("Envie um arquivo PDF.")
                else:
                    nome = secure_filename(arq.filename) or "orcamento.pdf"
                    salvo = f"{ordem_id}_{secrets.token_hex(4)}_{nome}"
                    arq.save(UPLOADS / salvo)
                    conn.execute(
                        """INSERT INTO arquivos
                        (ordem_id, nome_original, arquivo, tipo, criado_em)
                        VALUES (?, ?, ?, 'orcamento', ?)""",
                        (ordem_id, nome, salvo, agora()),
                    )
                    flash("PDF anexado.")
            return redirect(url_for("ordem", ordem_id=ordem_id))
        eventos = conn.execute(
            "SELECT * FROM eventos WHERE ordem_id = ? ORDER BY id DESC", (ordem_id,)
        ).fetchall()
        arquivos = conn.execute(
            "SELECT * FROM arquivos WHERE ordem_id = ? ORDER BY id DESC", (ordem_id,)
        ).fetchall()
    opcoes = "".join(
        f"<option {'selected' if s == item['status'] else ''}>{h(s)}</option>" for s in STATUS
    )
    hist = "".join(
        f"<li><strong>{h(e['status'])}</strong> · {h(e['criado_em'])}<br>{h(e['nota'] or '')}</li>"
        for e in eventos
    )
    pdfs = "".join(
        f"<li><a href='{url_for('baixar', ordem_id=ordem_id, arquivo_id=a['id'])}'>{h(a['nome_original'])}</a> · {h(a['criado_em'])}</li>"
        for a in arquivos
    )
    link = link_cliente(item["token"])
    zap = ""
    telefone = "".join(ch for ch in (item["telefone"] or "") if ch.isdigit())
    if telefone:
        msg = quote(f"Olá {item['cliente']}, acompanhe o veículo {item['placa']}: {link}")
        zap = f"<p><a class='btn sec' href='https://wa.me/{telefone}?text={msg}'>Enviar link no WhatsApp</a></p>"
    aviso_link = ""
    if not url_publica():
        aviso_link = "<p class='aviso'>Este link ainda não é público. No Railway, gere o domínio antes de enviar ao cliente.</p>"
    corpo = f"""
    <section class="card">
      <h1>OS #{item['id']} · {h(item['placa'])}</h1>
      <p>{h(item['cliente'])} · {h(item['marca'] or '')} {h(item['modelo'] or '')}</p>
      <p class="badge">{h(item['status'])}</p>
      {aviso_link}
      <p class="muted">Link do cliente. Envie só para o dono deste veículo:<br><a href="{h(link)}">{h(link)}</a></p>
      {zap}
    </section>
    <section class="card">
      <h2>Atualizar situação</h2>
      <form method="post">
        <input type="hidden" name="acao" value="status">
        <label>Nova situação</label>
        <select name="status">{opcoes}</select>
        <label>Nota para o cliente</label>
        <textarea name="nota" placeholder="Ex.: peça chegou, serviço segue amanhã."></textarea>
        <button>Salvar atualização</button>
      </form>
    </section>
    <section class="card">
      <h2>Anexar orçamento em PDF</h2>
      <form method="post" enctype="multipart/form-data">
        <input type="hidden" name="acao" value="pdf">
        <input type="file" name="pdf" accept="application/pdf,.pdf" required>
        <button>Anexar PDF</button>
      </form>
      <ul>{pdfs or '<li>Nenhum PDF ainda.</li>'}</ul>
    </section>
    <section class="card">
      <h2>Histórico</h2>
      <ul>{hist}</ul>
    </section>
    """
    return page(f"OS {ordem_id}", corpo, admin=True)


@app.route("/os/<int:ordem_id>/arquivo/<int:arquivo_id>")
@login_required
def baixar(ordem_id, arquivo_id):
    with db() as conn:
        arq = conn.execute(
            "SELECT * FROM arquivos WHERE id = ? AND ordem_id = ?",
            (arquivo_id, ordem_id),
        ).fetchone()
    if not arq:
        abort(404)
    return send_from_directory(UPLOADS, arq["arquivo"], as_attachment=True, download_name=arq["nome_original"])


@app.route("/c/<token>")
def cliente(token):
    with db() as conn:
        item = conn.execute("SELECT * FROM ordens WHERE token = ?", (token,)).fetchone()
        if not item:
            abort(404)
        eventos = conn.execute(
            "SELECT * FROM eventos WHERE ordem_id = ? ORDER BY id DESC", (item["id"],)
        ).fetchall()
        arquivos = conn.execute(
            "SELECT * FROM arquivos WHERE ordem_id = ? ORDER BY id DESC", (item["id"],)
        ).fetchall()
    hist = "".join(
        f"<li><strong>{h(e['status'])}</strong><br><span class='muted'>{h(e['criado_em'])}</span><br>{h(e['nota'] or '')}</li>"
        for e in eventos
    )
    pdfs = "".join(
        f"<li><a href='{url_for('cliente_pdf', token=token, arquivo_id=a['id'])}'>{h(a['nome_original'])}</a></li>"
        for a in arquivos
    )
    corpo = f"""
    <section class="card">
      <p class="muted">Acompanhamento do veículo</p>
      <h1>{h(item['placa'])}</h1>
      <p>{h(item['marca'] or '')} {h(item['modelo'] or '')}</p>
      <p class="badge">{h(item['status'])}</p>
      <p>{h(item['descricao'] or '')}</p>
      <p class="muted">Esta página atualiza sozinha a cada 8 segundos.</p>
    </section>
    <section class="card">
      <h2>Orçamentos</h2>
      <ul>{pdfs or '<li>Nenhum orçamento anexado ainda.</li>'}</ul>
    </section>
    <section class="card">
      <h2>Histórico</h2>
      <ul>{hist or '<li>Sem atualizações.</li>'}</ul>
    </section>
    <script>
      setInterval(() => location.reload(), 8000);
    </script>
    """
    return page(item["placa"], corpo)


@app.route("/c/<token>/pdf/<int:arquivo_id>")
def cliente_pdf(token, arquivo_id):
    with db() as conn:
        item = conn.execute("SELECT id FROM ordens WHERE token = ?", (token,)).fetchone()
        if not item:
            abort(404)
        arq = conn.execute(
            "SELECT * FROM arquivos WHERE id = ? AND ordem_id = ?",
            (arquivo_id, item["id"]),
        ).fetchone()
    if not arq:
        abort(404)
    return send_from_directory(UPLOADS, arq["arquivo"], as_attachment=True, download_name=arq["nome_original"])


def abrir_navegador():
    webbrowser.open(f"http://127.0.0.1:{PORTA}")


init_db()

if __name__ == "__main__":
    porta = int(os.environ.get("PORT", PORTA))
    no_servidor = bool(os.environ.get("PORT")) and NO_RAILWAY
    host = "0.0.0.0" if os.environ.get("PORT") else "127.0.0.1"
    print("Portal da oficina")
    if url_publica():
        print(f"Endereco publico: {url_publica()}")
    elif not no_servidor:
        print(f"Neste computador: http://127.0.0.1:{porta}")
        threading.Timer(1.2, abrir_navegador).start()
    app.run(host=host, port=porta, debug=False)
