"""Teste ponta a ponta (Entrega 4): as três camadas de software juntas.

    TERMINAL (código real da pasta esp32/, só o display e o botão simulados)
        <-- HTTP -->  BACKEND (FastAPI + SQLite, rodando com uvicorn)
        <-- HTTP -->  DASHBOARD (dashboard/dados.py e o app Streamlit)

O cliente compra com o botão e o cartão; o gerente vê a venda no painel,
manda mensagem, bloqueia, desbloqueia e muda um preço; o terminal recebe
cada comando pela consulta periódica, como na bancada.
"""

import os
import sys
import time

import pytest

from backend.tests.conftest import RAIZ
from backend.tests.test_esp32_contra_backend import backend, placa  # noqa: F401  (fixtures)

sys.path.insert(0, os.path.join(RAIZ, "dashboard"))

import dados  # noqa: E402  (dashboard)
from api import ClienteAPI  # noqa: E402  (esp32)
from carrinho import Carrinho  # noqa: E402
from catalogo import Catalogo  # noqa: E402
from comandos import Comandos  # noqa: E402
from fila_offline import FilaContingencia  # noqa: E402
import pagamentos as pg  # noqa: E402
import terminal as tela  # noqa: E402

TOQUE, SEGURAR = tela.TOQUE, tela.SEGURAR


class DisplayFalso:
    def __init__(self):
        self.linhas = []

    def mostrar(self, linhas):
        self.linhas = list(linhas)

    def texto(self):
        return "\n".join(self.linhas)


class SinalFalso:
    def __getattr__(self, nome):
        return lambda: None


def relogio_ms():
    return int(time.monotonic() * 1000)


@pytest.fixture
def sistema(backend, placa):  # noqa: F811
    url, servico = backend
    api_placa = ClienteAPI(url, "POS-01", tempo_limite_s=3)
    catalogo = Catalogo()
    catalogo.iniciar(api_placa)
    pagamentos = pg.Pagamentos(api_placa, FilaContingencia(), "POS-01", lambda: True)
    display = DisplayFalso()
    terminal = tela.Terminal(display, SinalFalso(), catalogo, Carrinho(), pagamentos,
                             conectado=lambda: True, relogio_ms=relogio_ms,
                             diferenca_ms=lambda a, b: a - b, pin_operador="2580")
    comandos = Comandos(api_placa, terminal, catalogo)
    painel = dados.ClienteAPI(url)
    return {"url": url, "api_placa": api_placa, "terminal": terminal, "display": display,
            "comandos": comandos, "painel": painel, "catalogo": catalogo}


def comprar_com_o_botao(terminal, display, posicao_do_produto, uid):
    """Desce até o produto, segura (inclui), desce até PAGAR, segura, aproxima o cartão."""
    for _ in range(posicao_do_produto):
        terminal.tecla(TOQUE)
    terminal.tecla(SEGURAR)
    while not any(linha.startswith(">PAGAR") for linha in display.linhas):
        terminal.tecla(TOQUE)
    terminal.tecla(SEGURAR)
    assert terminal.aguardando_cartao()
    terminal.cartao(uid)
    return display.linhas[0].strip()


def test_ponta_a_ponta(sistema):
    terminal, display, painel, comandos = sistema["terminal"], sistema["display"], sistema["painel"], sistema["comandos"]

    # 1. Cliente compra um Café com o botão e o cartão branco
    assert ">Cafe" in display.linhas[1] and "5,00" in display.linhas[1]
    assert comprar_com_o_botao(terminal, display, 0, "532F07FE") == "APROVADA"
    terminal.tecla(TOQUE)                                   # volta ao catálogo

    # 2. O painel vê a venda
    transacoes = painel.transacoes()
    numeros = dados.indicadores(transacoes)
    assert numeros["vendas"] == 1 and numeros["faturamento_centavos"] == 500
    assert painel.mais_vendidos()[0]["nome"] == "Café"
    assert transacoes[0]["titular"] == "Cliente do cartão branco"

    # 3. Uma recusa também aparece no painel, com o motivo
    assert comprar_com_o_botao(terminal, display, 5, "93892B4F") == "RECUSADA"   # Sanduíche, saldo R$ 5
    terminal.tecla(TOQUE)
    terminal.tecla(tela.SEGURAR_LONGO)                     # limpa o carrinho
    recusas = dados.recusas_por_motivo(painel.transacoes())
    assert recusas == [{"motivo": "Saldo insuficiente", "quantidade": 1}]

    # 4. Gerente manda uma mensagem; a placa recebe na próxima consulta
    painel.enviar_comando("POS-01", "mensagem", texto="Promoção do dia", segundos=5)
    assert comandos.verificar() is True
    assert terminal.estado == tela.MENSAGEM and "Promocao do dia" in display.texto()
    terminal.tecla(TOQUE)
    assert painel.comandos()[0]["status"] == "executado"

    # 5. Gerente bloqueia: o backend recusa na hora e a placa trava ao consultar
    painel.enviar_comando("POS-01", "bloquear", motivo="Manutenção")
    situacao = {t["codigo"]: t for t in painel.terminais()}["POS-01"]
    assert dados.situacao_do_terminal(situacao) == ("critico", "Bloqueado")
    comandos.verificar()
    assert terminal.bloqueado() and "BLOQUEADO" in display.texto()
    painel.enviar_comando("POS-01", "desbloquear")
    comandos.verificar()
    assert terminal.estado == tela.CATALOGO

    # 6. Gerente muda o preço do Café e avisa o terminal
    painel.alterar_produto("1", preco_centavos=400)
    painel.enviar_comando("POS-01", "atualizar_catalogo")
    comandos.verificar()
    assert "4,00" in display.linhas[1]
    assert comprar_com_o_botao(terminal, display, 0, "532F07FE") == "APROVADA"
    assert dados.indicadores(painel.transacoes())["faturamento_centavos"] == 900

    # 7. O painel exporta o relatório do período (Excel e CSV), com as vendas
    import io
    from openpyxl import load_workbook
    livro = load_workbook(io.BytesIO(painel.exportar("xlsx")))
    assert livro["Resumo"]["B8"].value == 9.0                             # faturamento: R$ 5 + R$ 4
    assert painel.exportar("csv").decode("utf-8-sig").count("Aprovada") == 2

    # 8. Telemetria da placa aparece no painel como "Online"
    assert sistema["api_placa"].enviar_telemetria({"online": True, "rssi": -60, "memoria_livre": 98000,
                                                   "fila_pendente": 0})
    situacao = {t["codigo"]: t for t in painel.terminais()}["POS-01"]
    assert dados.situacao_do_terminal(situacao) == ("bom", "Online")
    assert situacao["ultima_telemetria"]["rssi"] == -60


def test_fraude_bloqueia_e_o_gerente_libera(sistema):
    """Extra: quatro compras seguidas com o mesmo cartão. A quarta é recusada
    por segurança, o painel mostra o alerta e o gerente libera o cartão."""
    terminal, display, painel = sistema["terminal"], sistema["display"], sistema["painel"]
    for _ in range(3):
        assert comprar_com_o_botao(terminal, display, 1, "A1B2C3D4") == "APROVADA"     # Água
        terminal.tecla(TOQUE)
    assert comprar_com_o_botao(terminal, display, 1, "A1B2C3D4") == "RECUSADA"
    assert "Cartao bloqueado" in display.texto() and "por seguranca" in display.texto()
    terminal.tecla(TOQUE)

    alertas = dados.alertas_de_fraude(painel.cartoes_bloqueados())
    assert [a["uid_rfid"] for a in alertas] == ["A1B2C3D4"]
    assert alertas[0]["motivo_bloqueio"] == "Suspeita de fraude: 4 compras em menos de 60 s"

    painel.desbloquear_cartao("A1B2C3D4")
    assert dados.alertas_de_fraude(painel.cartoes_bloqueados()) == []
    terminal.tecla(tela.SEGURAR)                            # o carrinho continuava com a Água
    while not terminal.aguardando_cartao():
        terminal.tecla(TOQUE)
        if any(linha.startswith(">PAGAR") for linha in display.linhas):
            terminal.tecla(SEGURAR)
    terminal.cartao("A1B2C3D4")
    assert display.linhas[0].strip() == "APROVADA"


def test_app_streamlit_contra_o_backend(sistema, monkeypatch):
    """O app do painel roda inteiro (sem navegador) e mostra a venda."""
    pytest.importorskip("streamlit")
    from streamlit.testing.v1 import AppTest
    comprar_com_o_botao(sistema["terminal"], sistema["display"], 0, "532F07FE")
    monkeypatch.setenv("BYTEBANK_API_URL", sistema["url"])
    app = AppTest.from_file(os.path.join(RAIZ, "dashboard", "app.py"), default_timeout=30).run()
    assert not app.exception
    assert not app.error
    textos = " ".join(elemento.value for elemento in app.markdown)
    assert "R$ 5,00" in textos and "Vendas aprovadas" in textos
    assert "Faturamento por hora" in [s.value for s in app.subheader]


def test_painel_repoe_estoque_sem_perder_venda(sistema, monkeypatch):
    """Pelo formulário da aba Estoque: reposição soma; correção com valor
    desatualizado é barrada (409) e passa no segundo clique."""
    pytest.importorskip("streamlit")
    from streamlit.testing.v1 import AppTest
    monkeypatch.setenv("BYTEBANK_API_URL", sistema["url"])
    painel = sistema["painel"]
    estoque_do_cafe = lambda: painel.produtos()[0]["estoque"]                # noqa: E731
    app = AppTest.from_file(os.path.join(RAIZ, "dashboard", "app.py"), default_timeout=30).run()

    # Chegou mercadoria: +20
    app.radio(key="acao_estoque").set_value("Chegou mercadoria").run()
    app.number_input(key="unidades_1").set_value(20).run()
    salvar = [b for b in app.button if b.label == "Salvar"][0]
    salvar.click().run()
    assert estoque_do_cafe() == 70
    assert any("+20 Café: estoque agora 70 un." in s.value for s in app.success)

    # Corrigir contagem: o gerente abre a tela (vê 70) e o caixa vende 1 nesse meio-tempo
    app.radio(key="acao_estoque").set_value("Corrigir contagem").run()
    comprar_com_o_botao(sistema["terminal"], sistema["display"], 0, "532F07FE")
    sistema["terminal"].tecla(TOQUE)
    app.number_input(key="total_1").set_value(60).run()
    [b for b in app.button if b.label == "Salvar"][0].click().run()
    assert estoque_do_cafe() == 69                                            # a venda não sumiu
    assert "O estoque mudou de 70 para 69" in app.warning[0].value
    [b for b in app.button if b.label == "Salvar"][0].click().run()          # conferiu: salva de novo
    assert estoque_do_cafe() == 60
    assert not app.exception


def test_painel_sem_backend_mostra_aviso(monkeypatch):
    pytest.importorskip("streamlit")
    from streamlit.testing.v1 import AppTest
    monkeypatch.setenv("BYTEBANK_API_URL", "http://127.0.0.1:9")
    app = AppTest.from_file(os.path.join(RAIZ, "dashboard", "app.py"), default_timeout=30).run()
    assert not app.exception
    assert "Sem conexão com o backend" in app.error[0].value
