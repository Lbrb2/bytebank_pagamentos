"""Detecção de fraude (extra): regras, bloqueio preventivo atômico e desbloqueio."""

import pytest

from backend.antifraude import JANELA_S, Antifraude
from backend.tests.conftest import venda


class Relogio:
    def __init__(self):
        self.agora = 1000.0

    def __call__(self):
        return self.agora


# ---------------------------------------------------------------- regras (unidade)

def test_regra_1_quarta_compra_em_60_s_e_suspeita():
    relogio = Relogio()
    antifraude = Antifraude(relogio)
    for _ in range(3):
        assert antifraude.avaliar("532F07FE", 500) is None
        relogio.agora += 10
    assert antifraude.avaliar("532F07FE", 500) == "4 compras em menos de 60 s"


def test_regra_1_janela_desliza():
    relogio = Relogio()
    antifraude = Antifraude(relogio)
    for _ in range(3):
        antifraude.avaliar("532F07FE", 500)
        relogio.agora += 25                     # 0, 25, 50 -> a 4ª em 75 s
    assert antifraude.avaliar("532F07FE", 500) is None   # a de 0 s já saiu da janela
    relogio.agora += JANELA_S + 1
    assert antifraude.avaliar("532F07FE", 500) is None


def test_regra_1_cada_cartao_tem_sua_janela():
    antifraude = Antifraude(Relogio())
    for uid in ("A", "B", "C", "D"):
        for _ in range(3):
            assert antifraude.avaliar(uid, 500) is None


def test_vendas_da_fila_offline_nao_contam():
    antifraude = Antifraude(Relogio())
    for _ in range(10):
        assert antifraude.avaliar("532F07FE", 500, origem="contingencia") is None
    assert antifraude.avaliar("532F07FE", 500) is None


def test_regra_2_valor_muito_acima_da_media():
    relogio = Relogio()
    antifraude = Antifraude(relogio)
    for _ in range(3):
        antifraude.registrar_aprovada("532F07FE", 600)        # média R$ 6,00
    assert antifraude.avaliar("532F07FE", 2900) is None       # abaixo de R$ 30: nunca suspeito
    relogio.agora += 100
    motivo = antifraude.avaliar("532F07FE", 6000)
    assert motivo == "valor de R$ 60,00 acima de 5x a média (R$ 6,00)"
    relogio.agora += 100
    assert antifraude.avaliar("532F07FE", 2900) is None


def test_regra_2_precisa_de_historico_e_usa_so_as_ultimas_10():
    relogio = Relogio()
    antifraude = Antifraude(relogio)
    antifraude.registrar_aprovada("X", 500)
    antifraude.registrar_aprovada("X", 500)
    assert antifraude.avaliar("X", 10000) is None             # só 2 compras: ainda não conhece o cliente
    for _ in range(10):
        antifraude.registrar_aprovada("X", 3000)              # as de R$ 5 saem da janela de 10
    relogio.agora += 100
    assert antifraude.avaliar("X", 14000) is None             # média R$ 30: 5x = R$ 150


# ---------------------------------------------------------------- pela API

def test_quarta_compra_rapida_bloqueia_o_cartao(cliente, banco):
    for numero in range(3):
        assert cliente.post("/transacoes", json=venda("V{}".format(numero))).status_code == 200
    quarta = cliente.post("/transacoes", json=venda("V3"))
    assert quarta.status_code == 422
    assert quarta.json()["motivo"] == "Cartão bloqueado por segurança"
    cartao = banco.execute("SELECT status, motivo_bloqueio, bloqueado_em FROM cartoes "
                           "WHERE uid_rfid = '532F07FE'").fetchone()
    assert cartao["status"] == "bloqueado" and cartao["bloqueado_em"]
    assert cartao["motivo_bloqueio"] == "Suspeita de fraude: 4 compras em menos de 60 s"
    # a 4ª não debitou nada
    assert banco.execute("SELECT saldo_centavos FROM contas WHERE id = 1").fetchone()[0] == 100000 - 1500
    assert banco.execute("SELECT COUNT(*) FROM logs_auditoria WHERE evento = 'fraude_suspeita'").fetchone()[0] == 1
    # as próximas tentativas continuam recusadas
    assert cliente.post("/transacoes", json=venda("V4")).json()["motivo"] == "Cartão bloqueado por segurança"


def test_painel_lista_e_desbloqueia(cliente):
    for numero in range(4):
        cliente.post("/transacoes", json=venda("V{}".format(numero)))
    bloqueados = {c["uid_rfid"]: c for c in cliente.get("/cartoes/bloqueados").json()["cartoes"]}
    assert bloqueados["532F07FE"]["motivo_bloqueio"].startswith("Suspeita de fraude")
    assert bloqueados["DEADBEEF"]["motivo_bloqueio"] == "Bloqueado pelo banco (teste)"
    assert cliente.post("/cartoes/532f07fe/desbloquear").json() == {"uid": "532F07FE", "status": "ativo"}
    assert cliente.post("/transacoes", json=venda("V9")).status_code == 200    # janela zerada
    assert "532F07FE" not in {c["uid_rfid"] for c in cliente.get("/cartoes/bloqueados").json()["cartoes"]}
    assert cliente.post("/cartoes/FFFFFFFF/desbloquear").status_code == 404


def test_valor_fora_do_padrao_bloqueia(servico, banco):
    relogio = Relogio()
    servico.antifraude._relogio = relogio
    for numero in range(3):
        servico.processar_venda(venda("V{}".format(numero), itens=(("1", 1),)))   # R$ 5,00 cada
        relogio.agora += 120
    codigo, resposta = servico.processar_venda(venda("V3", itens=(("6", 5),), senha="1234"))     # R$ 60,00
    assert codigo == 422 and resposta["motivo"] == "Cartão bloqueado por segurança"
    motivo = banco.execute("SELECT motivo_bloqueio FROM cartoes WHERE uid_rfid = '532F07FE'").fetchone()[0]
    assert motivo == "Suspeita de fraude: valor de R$ 60,00 acima de 5x a média (R$ 5,00)"


def test_sincronizacao_da_fila_offline_nao_bloqueia(cliente):
    for numero in range(8):
        resposta = cliente.post("/transacoes", json=venda("F{}".format(numero), origem="contingencia"))
        assert resposta.status_code == 200


def test_media_e_refeita_quando_o_servidor_reinicia(tmp_path):
    from fastapi.testclient import TestClient
    from backend.app import criar_app
    caminho = str(tmp_path / "persistente.db")
    primeiro = TestClient(criar_app(caminho))
    for numero in range(3):
        primeiro.post("/transacoes", json=venda("V{}".format(numero)))
    app = criar_app(caminho)                                 # "reinicia" o servidor
    historico = app.state.servico.antifraude._compras.buscar("532F07FE")
    assert list(historico[0]) == [500, 500, 500] and historico[1] == 1500


@pytest.mark.parametrize("uid", ["DEADBEEF"])
def test_bloqueio_do_banco_continua_com_a_mensagem_antiga(cliente, uid):
    assert cliente.post("/transacoes", json=venda("B1", uid=uid)).json()["motivo"] == "Cartão bloqueado"


def test_banco_antigo_ganha_as_colunas_novas(tmp_path):
    """Um banco criado com o schema da Entrega 3 é atualizado sem perder dados."""
    from backend import banco as modulo_banco
    caminho = str(tmp_path / "antigo.db")
    conexao = modulo_banco.conectar(caminho)
    conexao.execute("ALTER TABLE cartoes DROP COLUMN motivo_bloqueio")
    conexao.execute("ALTER TABLE cartoes DROP COLUMN bloqueado_em")
    conexao.close()
    conexao = modulo_banco.conectar(caminho)
    colunas = {linha["name"] for linha in conexao.execute("PRAGMA table_info(cartoes)")}
    assert {"motivo_bloqueio", "bloqueado_em"} <= colunas
    assert conexao.execute("SELECT COUNT(*) FROM cartoes").fetchone()[0] == 5
