"""Senha do cartão na placa: o código real do terminal (esp32/), com o botão
simulado, contra o backend de verdade."""

import json
import os

from backend.tests.test_esp32_contra_backend import backend, placa  # noqa: F401  (fixtures)
from backend.tests.test_ponta_a_ponta import DisplayFalso, SinalFalso, relogio_ms, sistema  # noqa: F401

import pagamentos as pg  # noqa: E402  (esp32)
import terminal as tela  # noqa: E402
from fila_offline import FilaContingencia  # noqa: E402

TOQUE, SEGURAR, SEGURAR_LONGO = tela.TOQUE, tela.SEGURAR, tela.SEGURAR_LONGO
SANDUICHE = 5          # posição na lista: Café, Água, Suco, Pão de queijo, Bolo, Sanduíche


def dois_sanduiches_ate_o_cartao(terminal, display, uid="532F07FE"):
    """R$ 24,00 no carrinho, PAGAR e cartão aproximado."""
    for _ in range(SANDUICHE):
        terminal.tecla(TOQUE)
    terminal.tecla(SEGURAR)
    terminal.tecla(SEGURAR)                       # o cursor fica no item: inclui de novo
    while not any(linha.startswith(">PAGAR") for linha in display.linhas):
        terminal.tecla(TOQUE)
    terminal.tecla(SEGURAR)
    terminal.cartao(uid)


def digitar_com_o_botao(terminal, senha):
    for digito in senha:
        for _ in range(int(digito)):
            terminal.tecla(TOQUE)
        terminal.tecla(SEGURAR)


def test_senha_certa_com_o_botao(sistema):  # noqa: F811
    terminal, display = sistema["terminal"], sistema["display"]
    dois_sanduiches_ate_o_cartao(terminal, display)
    assert terminal.estado == tela.SENHA
    assert display.linhas[0].strip() == "SENHA DO CARTAO" and "24,00" in display.linhas[2]
    assert display.linhas[4].strip() == "0 _ _ _"
    for _ in range(3):
        terminal.tecla(TOQUE)
    assert display.linhas[4].strip() == "3 _ _ _"
    terminal.tecla(SEGURAR)
    assert display.linhas[4].strip() == "* 0 _ _"             # confirmado vira *
    terminal.tecla("*")                                        # (teclado) apaga
    assert display.linhas[4].strip() == "0 _ _ _"
    digitar_com_o_botao(terminal, "1234")
    assert display.linhas[0].strip() == "APROVADA"
    assert sistema["painel"].transacoes()[0]["total_centavos"] == 2400


def test_senha_errada_mantem_o_carrinho(sistema):  # noqa: F811
    terminal, display = sistema["terminal"], sistema["display"]
    dois_sanduiches_ate_o_cartao(terminal, display)
    for tecla in "9999":                                       # pelo teclado
        terminal.tecla(tecla)
    assert display.linhas[0].strip() == "RECUSADA"
    assert "Senha incorreta" in display.texto()
    terminal.tecla(TOQUE)                                      # volta ao catálogo
    assert "2 itens" in display.texto()                       # carrinho mantido


def test_segurar_5_s_cancela_a_senha(sistema):  # noqa: F811
    terminal, display = sistema["terminal"], sistema["display"]
    dois_sanduiches_ate_o_cartao(terminal, display)
    terminal.tecla(SEGURAR)
    terminal.tecla(SEGURAR_LONGO)
    assert terminal.estado == tela.PAGAMENTO and terminal.aguardando_cartao()


def test_ate_20_reais_nao_pede_senha(sistema):  # noqa: F811
    terminal, display = sistema["terminal"], sistema["display"]
    terminal.tecla(SEGURAR)                                    # 1 Café
    while not any(linha.startswith(">PAGAR") for linha in display.linhas):
        terminal.tecla(TOQUE)
    terminal.tecla(SEGURAR)
    terminal.cartao("532F07FE")
    assert display.linhas[0].strip() == "APROVADA"


def test_senha_nunca_vai_para_a_flash(placa):  # noqa: F811
    """Servidor fora do ar: a venda vai para a fila offline SEM a senha."""
    class ApiForaDoAr:
        def __init__(self):
            self.enviados = []

        def enviar_transacao(self, transacao):
            self.enviados.append(transacao)
            return None, "sem resposta"

    api = ApiForaDoAr()
    pagamentos = pg.Pagamentos(api, FilaContingencia(), "POS-01", lambda: True)
    resultado = pagamentos.pagar("532F07FE", [{"codigo": "6", "quantidade": 2}], 2400, "1234")
    assert resultado["situacao"] == pg.OFFLINE
    assert api.enviados[0]["senha"] == "1234"                 # a tentativa online levou a senha
    with open(os.path.join(str(placa), "fila_offline.json")) as arquivo:
        gravado = arquivo.read()
    assert "1234" not in gravado and "senha" not in gravado
    assert json.loads(gravado)[0]["origem"] == "contingencia"


def test_terceira_senha_errada_mostra_o_bloqueio(sistema):  # noqa: F811
    terminal, display = sistema["terminal"], sistema["display"]
    dois_sanduiches_ate_o_cartao(terminal, display)
    for tentativa in range(3):
        if tentativa:
            terminal.tecla(TOQUE)                              # sai do RESULTADO
            while not any(linha.startswith(">PAGAR") for linha in display.linhas):
                terminal.tecla(TOQUE)
            terminal.tecla(SEGURAR)
            terminal.cartao("532F07FE")
        for tecla in "9999":
            terminal.tecla(tecla)
    assert "Senha incorreta" in display.texto() and "Cartao bloqueado" in display.texto().replace("ã", "a")
    motivos = [t["motivo"] for t in sistema["painel"].transacoes()]
    assert motivos == ["Senha incorreta"] * 3                  # uma categoria só no painel


def test_compra_inteira_pelo_teclado(sistema):  # noqa: F811
    """Com o teclado 4x4: 6 6 # (dois Sanduíches e pagar), cartão, 1 2 3 4."""
    terminal, display = sistema["terminal"], sistema["display"]
    for tecla in "66#":
        terminal.tecla(tecla)
    assert terminal.aguardando_cartao()
    terminal.cartao("532F07FE")
    assert terminal.estado == tela.SENHA
    for tecla in "1234":
        terminal.tecla(tecla)
    assert display.linhas[0].strip() == "APROVADA"
