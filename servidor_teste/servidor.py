"""
servidor.py - Servidor de TESTE do ByteBank Pay (Entrega 2)

Recebe os pacotes JSON enviados pelo ESP32 com urequests.post() e responde
como o backend responderia. Serve para testar a placa antes de o backend
de verdade (FastAPI + SQLite, Entrega 3) existir.

Usa só a biblioteca padrão do Python: não é preciso instalar nada.

    python servidor_teste/servidor.py

Os dados ficam em memória e são perdidos ao fechar o servidor.

Rotas usadas pelo terminal:
    GET  /                                  confere se o servidor está no ar
    GET  /produtos                          catálogo (com o estoque)
    POST /transacoes                        venda: valida cartão e itens, debita o saldo
    POST /estornos                          cancela uma venda aprovada (modo operador)
    POST /telemetria                        estado do terminal
    GET  /terminais/<id>/resumo             resumo do caixa (modo operador)
    GET  /terminais/<id>/comandos           próximo comando remoto pendente
    POST /terminais/<id>/comandos/<n>/confirmacao   terminal avisa que executou

Rotas usadas pelo gerente (hoje pela ferramenta enviar_comando.py; na
Entrega 4, pelo dashboard):
    POST  /terminais/<id>/comandos          coloca um comando na fila do terminal
    PATCH /produtos/<codigo>                muda preço e/ou estoque
"""

import json
import socket
import threading
from collections import deque
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORTA = 8000

# Tabela hash: código do produto -> produto. Busca em O(1).
PRODUTOS = {
    "1": {"codigo": "1", "nome": "Café", "preco_centavos": 500, "estoque": 50},
    "2": {"codigo": "2", "nome": "Água", "preco_centavos": 300, "estoque": 50},
    "3": {"codigo": "3", "nome": "Suco", "preco_centavos": 750, "estoque": 30},
    "4": {"codigo": "4", "nome": "Pão de queijo", "preco_centavos": 450, "estoque": 40},
    "5": {"codigo": "5", "nome": "Bolo", "preco_centavos": 600, "estoque": 20},
    "6": {"codigo": "6", "nome": "Sanduíche", "preco_centavos": 1200, "estoque": 15},
    "7": {"codigo": "7", "nome": "Chocolate", "preco_centavos": 400, "estoque": 2},
}

# Tabela hash: UID do cartão RFID -> conta. Busca em O(1).
# >>> Troque os UIDs pelos dos cartões da bancada. Quando um cartão
#     desconhecido for lido, o servidor mostra o UID dele no console.
CARTOES = {
    "A1B2C3D4": {"titular": "Amanda Silva", "saldo_centavos": 100000, "bloqueado": False},
    "11223344": {"titular": "Caio Ferreira", "saldo_centavos": 1000, "bloqueado": False},
    "DEADBEEF": {"titular": "Douglas Alves", "saldo_centavos": 50000, "bloqueado": True},
}

# Tabela hash: id_transacao -> resposta já dada. É o que torna o servidor
# idempotente: a mesma venda enviada duas vezes só é cobrada uma vez.
PROCESSADAS = {}

# Tabela hash: id_transacao -> venda APROVADA (para estorno e resumo do caixa)
VENDAS = {}

# Tabela hash: id do terminal -> situação e FILA (FIFO) de comandos pendentes.
# O deque remove do início em O(1); os comandos são executados na ordem em
# que o gerente os enviou.
TERMINAIS = {}
TIPOS_DE_COMANDO = ("atualizar_catalogo", "bloquear", "desbloquear", "mensagem")

TELEMETRIA = []
_trava = threading.Lock()   # uma operação por vez: saldo e estoque nunca ficam inconsistentes
_proximo_id = [1]
_proximo_comando = [1]


def registrar(texto):
    print("[{}] {}".format(datetime.now().strftime("%H:%M:%S"), texto), flush=True)


def reais(centavos):
    texto = "{:,.2f}".format(centavos / 100)
    return "R$ " + texto.replace(",", "X").replace(".", ",").replace("X", ".")


def terminal(terminal_id):
    """Situação do terminal; criada na primeira vez que ele aparece."""
    if terminal_id not in TERMINAIS:
        TERMINAIS[terminal_id] = {"bloqueado": False, "comandos": deque()}
    return TERMINAIS[terminal_id]


# ---------------- Vendas ----------------

def processar_transacao(dados):
    """Devolve (codigo_http, resposta). Chamado com a trava adquirida."""
    obrigatorios = ("id_transacao", "terminal", "uid_cartao", "itens")
    faltando = [campo for campo in obrigatorios if campo not in dados]
    if faltando:
        return 400, {"status": "recusada", "motivo": "Pacote sem: " + ", ".join(faltando)}

    anterior = PROCESSADAS.get(dados["id_transacao"])
    if anterior is not None:
        registrar("Venda {} REPETIDA: devolvendo a resposta original, sem cobrar de novo.".format(
            dados["id_transacao"]))
        resposta = dict(anterior[1])
        resposta["repetida"] = True
        return anterior[0], resposta

    codigo, resposta = _cobrar(dados)
    PROCESSADAS[dados["id_transacao"]] = (codigo, resposta)
    return codigo, resposta


def _cobrar(dados):
    # Terminal bloqueado pelo gerente não faz vendas novas. As vendas feitas
    # offline ANTES do bloqueio ainda são aceitas quando chegam pela fila.
    if terminal(str(dados["terminal"]))["bloqueado"] and dados.get("origem") != "contingencia":
        return 422, {"status": "recusada", "motivo": "Terminal bloqueado"}

    conta = CARTOES.get(str(dados["uid_cartao"]).upper())
    if conta is None:
        registrar(">>> Cartão DESCONHECIDO: UID {}. Para aceitá-lo, inclua em CARTOES.".format(
            dados["uid_cartao"]))
        return 422, {"status": "recusada", "motivo": "Cartão não cadastrado"}
    if conta["bloqueado"]:
        return 422, {"status": "recusada", "motivo": "Cartão bloqueado"}

    itens = dados["itens"]
    if not isinstance(itens, list) or not itens:
        return 400, {"status": "recusada", "motivo": "Venda sem itens"}

    # Soma as quantidades por produto (o mesmo código pode vir em duas linhas)
    quantidades = {}
    for item in itens:
        if not isinstance(item, dict):
            return 400, {"status": "recusada", "motivo": "Item inválido"}
        codigo, quantidade = str(item.get("codigo")), item.get("quantidade")
        if codigo not in PRODUTOS:
            return 422, {"status": "recusada", "motivo": "Produto {} inexistente".format(codigo)}
        if not isinstance(quantidade, int) or isinstance(quantidade, bool) or quantidade <= 0:
            return 400, {"status": "recusada", "motivo": "Quantidade inválida"}
        quantidades[codigo] = quantidades.get(codigo, 0) + quantidade

    # O total é calculado AQUI, com os preços do servidor. O valor enviado
    # pelo terminal é ignorado: um terminal adulterado não escolhe o preço.
    total = 0
    for codigo, quantidade in quantidades.items():
        produto = PRODUTOS[codigo]
        if quantidade > produto["estoque"]:
            return 422, {"status": "recusada", "motivo": "Sem estoque de {}".format(produto["nome"])}
        total += produto["preco_centavos"] * quantidade

    if dados.get("total_centavos") not in (None, total):
        registrar("Aviso: o terminal calculou {}, o servidor cobrou {}.".format(
            reais(dados["total_centavos"]), reais(total)))

    if total > conta["saldo_centavos"]:
        return 422, {"status": "recusada", "motivo": "Saldo insuficiente"}

    # Só chega aqui se TUDO foi validado: debita e baixa o estoque juntos
    conta["saldo_centavos"] -= total
    for codigo, quantidade in quantidades.items():
        PRODUTOS[codigo]["estoque"] -= quantidade
    numero = _proximo_id[0]
    _proximo_id[0] += 1
    VENDAS[dados["id_transacao"]] = {
        "terminal": str(dados["terminal"]), "uid_cartao": str(dados["uid_cartao"]).upper(),
        "quantidades": quantidades, "total_centavos": total, "estornada": False,
    }
    return 200, {"status": "aprovada", "transacao_id": numero, "total_centavos": total,
                 "saldo_centavos": conta["saldo_centavos"], "titular": conta["titular"]}


def processar_estorno(dados):
    """Cancela uma venda aprovada: devolve o dinheiro e o estoque, juntos.

    Idempotente como as vendas: se o terminal reenviar o mesmo estorno
    (porque a resposta se perdeu), o dinheiro não é devolvido duas vezes.
    """
    id_transacao, terminal_id = dados.get("id_transacao"), dados.get("terminal")
    if not id_transacao or not terminal_id:
        return 400, {"status": "recusado", "motivo": "Informe id_transacao e terminal"}
    venda = VENDAS.get(id_transacao)
    if venda is None:
        return 404, {"status": "recusado", "motivo": "Venda não encontrada"}
    if venda["terminal"] != terminal_id:
        return 403, {"status": "recusado", "motivo": "Venda de outro terminal"}

    conta = CARTOES[venda["uid_cartao"]]
    if venda["estornada"]:
        return 200, {"status": "estornada", "repetido": True, "total_centavos": venda["total_centavos"],
                     "saldo_centavos": conta["saldo_centavos"]}

    conta["saldo_centavos"] += venda["total_centavos"]
    for codigo, quantidade in venda["quantidades"].items():
        PRODUTOS[codigo]["estoque"] += quantidade
    venda["estornada"] = True
    return 200, {"status": "estornada", "total_centavos": venda["total_centavos"],
                 "saldo_centavos": conta["saldo_centavos"]}


def resumo_do_caixa(terminal_id):
    resumo = {"vendas": 0, "total_centavos": 0, "estornos": 0, "estornado_centavos": 0}
    for venda in VENDAS.values():
        if venda["terminal"] != terminal_id:
            continue
        if venda["estornada"]:
            resumo["estornos"] += 1
            resumo["estornado_centavos"] += venda["total_centavos"]
        else:
            resumo["vendas"] += 1
            resumo["total_centavos"] += venda["total_centavos"]
    return resumo


# ---------------- Comandos remotos ----------------

def criar_comando(terminal_id, dados):
    tipo = dados.get("tipo")
    if tipo not in TIPOS_DE_COMANDO:
        return 400, {"erro": "tipo deve ser um de: " + ", ".join(TIPOS_DE_COMANDO)}
    comando = {"id": _proximo_comando[0], "tipo": tipo}
    _proximo_comando[0] += 1
    if tipo == "bloquear":
        comando["motivo"] = str(dados.get("motivo") or "Fale com o gerente")
    elif tipo == "mensagem":
        if not dados.get("texto"):
            return 400, {"erro": "mensagem precisa de texto"}
        comando["texto"] = str(dados["texto"])
        comando["segundos"] = dados.get("segundos", 10)

    situacao = terminal(terminal_id)
    # O bloqueio vale no servidor na hora: mesmo que o terminal demore a
    # buscar o comando, ele já não consegue aprovar vendas novas.
    if tipo == "bloquear":
        situacao["bloqueado"] = True
    elif tipo == "desbloquear":
        situacao["bloqueado"] = False
    situacao["comandos"].append(comando)
    return 201, comando


def confirmar_comando(terminal_id, numero, dados):
    fila = terminal(terminal_id)["comandos"]
    if fila and fila[0]["id"] == numero:
        fila.popleft()
        registrar("{} executou o comando {}: {}".format(terminal_id, numero, dados.get("resultado")))
    # Se não está mais na fila, a confirmação é repetida: responde ok também
    return 200, {"ok": True}


def alterar_produto(codigo, dados):
    produto = PRODUTOS.get(codigo)
    if produto is None:
        return 404, {"erro": "produto não encontrado"}
    for campo in ("preco_centavos", "estoque"):
        if campo in dados:
            valor = dados[campo]
            minimo = 1 if campo == "preco_centavos" else 0
            if not isinstance(valor, int) or isinstance(valor, bool) or valor < minimo:
                return 400, {"erro": "{} inválido".format(campo)}
    for campo in ("preco_centavos", "estoque"):
        if campo in dados:
            produto[campo] = dados[campo]
    return 200, produto


# ---------------- HTTP ----------------

def _partes(caminho):
    return [parte for parte in caminho.split("?")[0].split("/") if parte]


class Manipulador(BaseHTTPRequestHandler):
    def _responder(self, codigo, corpo):
        dados = json.dumps(corpo, ensure_ascii=False).encode("utf-8")
        self.send_response(codigo)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(dados)))
        self.end_headers()
        self.wfile.write(dados)

    def _ler_json(self):
        tamanho = int(self.headers.get("Content-Length") or 0)
        bruto = self.rfile.read(tamanho) if tamanho else b""
        return json.loads(bruto.decode("utf-8")) if bruto else {}

    def do_GET(self):
        partes = _partes(self.path)
        if not partes:
            self._responder(200, {"servico": "ByteBank Pay - servidor de teste", "ok": True})
        elif partes == ["produtos"]:
            with _trava:
                lista = [{k: p[k] for k in ("codigo", "nome", "preco_centavos", "estoque")}
                         for p in sorted(PRODUTOS.values(), key=lambda p: p["codigo"])]
            registrar("Catálogo enviado para {}".format(self.client_address[0]))
            self._responder(200, {"produtos": lista})
        elif len(partes) == 3 and partes[0] == "terminais" and partes[2] == "comandos":
            with _trava:
                fila = terminal(partes[1])["comandos"]
                comando = dict(fila[0]) if fila else None
            if comando is not None:
                registrar("Comando {} entregue a {}: {}".format(comando["id"], partes[1], comando["tipo"]))
            self._responder(200, {"comando": comando})
        elif len(partes) == 3 and partes[0] == "terminais" and partes[2] == "resumo":
            with _trava:
                resumo = resumo_do_caixa(partes[1])
            self._responder(200, resumo)
        else:
            self._responder(404, {"erro": "rota não encontrada"})

    def _corpo_json(self):
        try:
            dados = self._ler_json()
        except (ValueError, UnicodeDecodeError):
            registrar("Pacote com JSON inválido recebido de {}".format(self.client_address[0]))
            self._responder(400, {"status": "recusada", "motivo": "JSON inválido"})
            return None
        if not isinstance(dados, dict):
            self._responder(400, {"status": "recusada", "motivo": "JSON deve ser um objeto"})
            return None
        return dados

    def do_POST(self):
        dados = self._corpo_json()
        if dados is None:
            return
        partes = _partes(self.path)

        if partes == ["transacoes"]:
            registrar("Venda recebida:\n" + json.dumps(dados, indent=2, ensure_ascii=False))
            with _trava:
                codigo, resposta = processar_transacao(dados)
            registrar("Resposta HTTP {}: {}".format(codigo, json.dumps(resposta, ensure_ascii=False)))
        elif partes == ["estornos"]:
            with _trava:
                codigo, resposta = processar_estorno(dados)
            registrar("Estorno de {}: HTTP {} {}".format(
                dados.get("id_transacao"), codigo, json.dumps(resposta, ensure_ascii=False)))
        elif partes == ["telemetria"]:
            dados["recebida_em"] = datetime.now().isoformat(timespec="seconds")
            TELEMETRIA.append(dados)
            registrar("Telemetria: {}".format(json.dumps(dados, ensure_ascii=False)))
            codigo, resposta = 200, {"ok": True}
        elif len(partes) == 3 and partes[0] == "terminais" and partes[2] == "comandos":
            with _trava:
                codigo, resposta = criar_comando(partes[1], dados)
            registrar("Comando para {}: {}".format(partes[1], json.dumps(resposta, ensure_ascii=False)))
        elif (len(partes) == 5 and partes[0] == "terminais" and partes[2] == "comandos"
              and partes[4] == "confirmacao" and partes[3].isdigit()):
            with _trava:
                codigo, resposta = confirmar_comando(partes[1], int(partes[3]), dados)
        else:
            codigo, resposta = 404, {"erro": "rota não encontrada"}
        self._responder(codigo, resposta)

    def do_PATCH(self):
        dados = self._corpo_json()
        if dados is None:
            return
        partes = _partes(self.path)
        if len(partes) == 2 and partes[0] == "produtos":
            with _trava:
                codigo, resposta = alterar_produto(partes[1], dados)
            registrar("Produto {} alterado: HTTP {} {}".format(
                partes[1], codigo, json.dumps(resposta, ensure_ascii=False)))
            self._responder(codigo, resposta)
        else:
            self._responder(404, {"erro": "rota não encontrada"})

    def log_message(self, formato, *args):
        pass   # o registrar() já mostra o que importa


def ip_local():
    """IP deste computador na rede local: é ele que vai no API_URL do ESP32."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))   # não envia nada; só descobre a interface de saída
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


def main():
    servidor = ThreadingHTTPServer(("0.0.0.0", PORTA), Manipulador)
    print("=" * 60)
    print(" ByteBank Pay - servidor de TESTE (Entrega 2)")
    print("=" * 60)
    print(" No config.py do ESP32, use:")
    print('     API_URL = "http://{}:{}"'.format(ip_local(), PORTA))
    print(" O ESP32 e este computador precisam estar na MESMA rede.")
    print(" Comandos para o terminal: python ferramentas/enviar_comando.py")
    print(" Para encerrar: Ctrl+C")
    print("=" * 60, flush=True)
    try:
        servidor.serve_forever()
    except KeyboardInterrupt:
        print("\nServidor encerrado.")
    finally:
        servidor.server_close()


if __name__ == "__main__":
    main()
