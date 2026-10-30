"""
api.py - Comunicação HTTP com o backend (MicroPython)

Todas as chamadas devolvem (codigo_http, corpo):

    (200, {...})     o servidor respondeu
    (422, {...})     o servidor respondeu, recusando (regra de negócio)
    (None, "texto")  não houve resposta: rede fora, servidor desligado,
                     tempo esgotado. Nesse caso a venda vai para a fila.

Nenhuma exceção de rede escapa daqui: um cabo solto ou um roteador
reiniciando não pode derrubar o terminal.

Dois cuidados específicos do ESP32:
  * toda resposta é FECHADA (close) depois de lida. Cada resposta aberta
    prende um socket e memória; depois de algumas vendas, a placa travaria;
  * o corpo é enviado em bytes, com o tamanho correto. O urequests calcula
    o Content-Length pelo número de caracteres, e um texto com acento tem
    mais bytes do que caracteres.
"""

import json

try:
    import urequests as requests
except ImportError:
    import requests

TEMPO_LIMITE_S = 5


class ClienteAPI:
    def __init__(self, url_base, terminal_id, tempo_limite_s=TEMPO_LIMITE_S):
        self._url = url_base.rstrip("/")
        self._terminal_id = terminal_id
        self._tempo_limite = tempo_limite_s

    def _requisitar(self, metodo, caminho, dados=None):
        url = self._url + caminho
        parametros = {"headers": {"Content-Type": "application/json"}}
        if dados is not None:
            parametros["data"] = json.dumps(dados).encode("utf-8")

        resposta = None
        try:
            try:
                resposta = requests.request(metodo, url, timeout=self._tempo_limite, **parametros)
            except TypeError:
                # Firmwares antigos têm um urequests sem o parâmetro timeout
                resposta = requests.request(metodo, url, **parametros)
            codigo = resposta.status_code
            try:
                corpo = resposta.json()
            except ValueError:
                corpo = {}
            return codigo, corpo
        except OSError as erro:
            # Sem rede, servidor desligado, DNS, tempo esgotado...
            return None, "sem resposta do servidor ({})".format(erro)
        except ValueError as erro:
            # URL mal escrita no config.py (ex.: faltou o http://)
            return None, "API_URL inválida no config.py ({})".format(erro)
        finally:
            if resposta is not None:
                resposta.close()

    def obter_catalogo(self):
        """Lista de produtos, ou None se o servidor não respondeu direito."""
        codigo, corpo = self._requisitar("GET", "/produtos")
        if codigo == 200 and isinstance(corpo, dict) and isinstance(corpo.get("produtos"), list):
            return corpo["produtos"]
        if codigo is None:
            print("[API] Catálogo: {}".format(corpo))
        else:
            print("[API] Catálogo: resposta inesperada (HTTP {}).".format(codigo))
        return None

    def enviar_transacao(self, transacao):
        return self._requisitar("POST", "/transacoes", transacao)

    def enviar_telemetria(self, dados):
        dados = dict(dados)
        dados["terminal"] = self._terminal_id
        codigo, _ = self._requisitar("POST", "/telemetria", dados)
        return codigo == 200

    # ---------------- Modo operador ----------------

    def estornar(self, id_transacao):
        """Pede o cancelamento de uma venda aprovada. Devolve (codigo, corpo)."""
        return self._requisitar("POST", "/estornos",
                                {"id_transacao": id_transacao, "terminal": self._terminal_id})

    def obter_resumo(self):
        """Resumo do caixa deste terminal. Devolve (codigo, corpo)."""
        return self._requisitar("GET", "/terminais/{}/resumo".format(self._terminal_id))

    # ---------------- Comandos remotos ----------------

    def obter_comando(self):
        """Próximo comando pendente para este terminal.

        Devolve (respondeu, comando): respondeu é False se o servidor não
        respondeu; comando é None quando não há nada pendente.
        """
        codigo, corpo = self._requisitar("GET", "/terminais/{}/comandos".format(self._terminal_id))
        if codigo != 200 or not isinstance(corpo, dict):
            return False, None
        comando = corpo.get("comando")
        return True, comando if isinstance(comando, dict) else None

    def confirmar_comando(self, numero, resultado):
        codigo, _ = self._requisitar(
            "POST", "/terminais/{}/comandos/{}/confirmacao".format(self._terminal_id, numero),
            {"resultado": resultado})
        return codigo == 200
