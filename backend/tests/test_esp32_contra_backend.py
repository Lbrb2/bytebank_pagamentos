"""Teste de integração: o código da PLACA (pasta esp32/) conversando com o
backend de verdade, por HTTP, como no laboratório.

Sobe o backend com uvicorn numa porta livre e usa os módulos do ESP32
(api.py, catalogo.py, pagamentos.py, fila_offline.py, comandos.py) sem
nenhuma alteração. O urequests da placa é trocado pelo da pasta
ferramentas/compat, que faz a mesma coisa no computador.
"""

import os
import socket
import sys
import threading
import time

import pytest
import uvicorn

from backend.app import criar_app
from backend.tests.conftest import RAIZ

sys.path.insert(0, os.path.join(RAIZ, "ferramentas", "compat"))
sys.path.insert(1, os.path.join(RAIZ, "esp32"))

from api import ClienteAPI  # noqa: E402
from catalogo import Catalogo  # noqa: E402
from comandos import Comandos  # noqa: E402
from fila_offline import FilaContingencia  # noqa: E402
import pagamentos as pg  # noqa: E402


def porta_livre():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def backend(tmp_path):
    app = criar_app(str(tmp_path / "integracao.db"))
    porta = porta_livre()
    servidor = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=porta, log_level="warning"))
    thread = threading.Thread(target=servidor.run, daemon=True)
    thread.start()
    for _ in range(100):
        if servidor.started:
            break
        time.sleep(0.05)
    yield "http://127.0.0.1:{}".format(porta), app.state.servico
    servidor.should_exit = True
    thread.join(5)


@pytest.fixture
def placa(tmp_path, monkeypatch):
    """Pasta que faz o papel da flash do ESP32 (catalogo.json, fila_offline.json...)."""
    flash = tmp_path / "flash"
    flash.mkdir()
    monkeypatch.chdir(flash)
    return flash


class TerminalFalso:
    def __init__(self):
        self.eventos = []

    def bloquear(self, motivo):
        self.eventos.append(("bloquear", motivo))

    def desbloquear(self):
        self.eventos.append(("desbloquear",))

    def mostrar_mensagem(self, texto, duracao_ms):
        self.eventos.append(("mensagem", texto, duracao_ms))
        return True

    def catalogo_atualizado(self):
        self.eventos.append(("catalogo",))


def test_fluxo_completo_da_placa(backend, placa):
    url, servico = backend
    api = ClienteAPI(url, "POS-01", tempo_limite_s=3)
    wifi = {"ligado": True}

    # Catálogo vem do backend, com estoque
    catalogo = Catalogo()
    assert catalogo.iniciar(api) == "servidor"
    assert catalogo.buscar("7")["nome"] == "Chocolate" and catalogo.estoque("7") == 2

    fila = FilaContingencia()
    pagamentos = pg.Pagamentos(api, fila, "POS-01", lambda: wifi["ligado"])

    # Venda online aprovada
    resultado = pagamentos.pagar("532F07FE", [{"codigo": "1", "quantidade": 2}, {"codigo": "2", "quantidade": 1}], 1300)
    assert resultado == {"situacao": "aprovada", "total_centavos": 1300, "saldo_centavos": 98700}

    # Recusa com motivo do servidor
    recusa = pagamentos.pagar("93892B4F", [{"codigo": "6", "quantidade": 1}], 1200)
    assert recusa == {"situacao": "recusada", "motivo": "Saldo insuficiente"}

    # Sem Wi-Fi: vai para a fila offline; depois sincroniza
    wifi["ligado"] = False
    offline = pagamentos.pagar("532F07FE", [{"codigo": "3", "quantidade": 1}], 750)
    assert offline["situacao"] == "offline" and len(fila) == 1
    wifi["ligado"] = True
    assert pagamentos.sincronizar() is True and len(fila) == 0
    linha = servico._con.execute("SELECT origem, status FROM transacoes ORDER BY id DESC LIMIT 1").fetchone()
    assert tuple(linha) == ("contingencia", "aprovada")

    # Reenvio da mesma venda: o backend reconhece e não cobra de novo
    venda = pagamentos.montar_transacao("532F07FE", [{"codigo": "5", "quantidade": 1}], 600)
    codigo1, corpo1 = api.enviar_transacao(venda)
    codigo2, corpo2 = api.enviar_transacao(venda)
    assert codigo1 == codigo2 == 200 and corpo2["repetida"] is True
    assert corpo2["saldo_centavos"] == corpo1["saldo_centavos"]

    # Modo operador: estorno da última venda e resumo do caixa
    pagamentos.ultima_venda = {"id_transacao": venda["id_transacao"], "itens": venda["itens"],
                               "total_centavos": 600}
    assert pagamentos.estornar_ultima() == (True, "Venda cancelada")
    resumo, erro = pagamentos.resumo_do_caixa()
    assert erro is None
    assert resumo["vendas"] == 2 and resumo["estornos"] == 1 and resumo["estornado_centavos"] == 600

    # Telemetria
    assert api.enviar_telemetria({"online": True, "rssi": -60, "fila_pendente": 0}) is True


def test_comandos_do_gerente_chegam_na_placa(backend, placa):
    url, servico = backend
    api = ClienteAPI(url, "POS-01", tempo_limite_s=3)
    terminal = TerminalFalso()
    catalogo = Catalogo()
    catalogo.iniciar(api)
    comandos = Comandos(api, terminal, catalogo)

    assert comandos.verificar() is True and terminal.eventos == []
    servico.criar_comando("POS-01", "bloquear", {"motivo": "Manutencao"})
    servico.alterar_produto("1", preco_centavos=450)
    servico.criar_comando("POS-01", "atualizar_catalogo", {})
    servico.criar_comando("POS-01", "mensagem", {"texto": "Promo", "segundos": 5})
    servico.criar_comando("POS-01", "desbloquear", {})
    for _ in range(4):
        comandos.verificar()
    assert terminal.eventos == [("bloquear", "Manutencao"), ("catalogo",), ("mensagem", "Promo", 5000),
                                ("desbloquear",)]
    assert catalogo.buscar("1")["preco_centavos"] == 450
    assert servico.proximo_comando("POS-01") is None                 # todos confirmados


def test_servidor_desligado_vira_contingencia(placa):
    api = ClienteAPI("http://127.0.0.1:{}".format(porta_livre()), "POS-01", tempo_limite_s=1)
    fila = FilaContingencia()
    pagamentos = pg.Pagamentos(api, fila, "POS-01", lambda: True)
    assert pagamentos.pagar("532F07FE", [{"codigo": "1", "quantidade": 1}], 500)["situacao"] == "offline"
    assert len(fila) == 1
