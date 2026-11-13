"""Testes da API: regras de negócio, atomicidade, idempotência e auditoria."""

import sqlite3
import threading

import pytest

from backend.tests.conftest import venda

BRANCO, CHAVEIRO, BLOQUEADO = "532F07FE", "93892B4F", "DEADBEEF"


def saldo(banco, uid):
    return banco.execute("SELECT ct.saldo_centavos FROM contas ct JOIN cartoes c ON c.conta_id = ct.id "
                         "WHERE c.uid_rfid = ?", (uid,)).fetchone()[0]


def estoque(banco, codigo):
    return banco.execute("SELECT estoque FROM produtos WHERE codigo = ?", (codigo,)).fetchone()[0]


def contar(banco, tabela, onde="1=1", parametros=()):
    return banco.execute("SELECT COUNT(*) FROM {} WHERE {}".format(tabela, onde), parametros).fetchone()[0]


# ---------------------------------------------------------------- catálogo

def test_catalogo_com_estoque(cliente):
    produtos = cliente.get("/produtos").json()["produtos"]
    assert [p["codigo"] for p in produtos] == list("1234567")
    assert produtos[0] == {"codigo": "1", "nome": "Café", "preco_centavos": 500, "estoque": 50}


# ---------------------------------------------------------------- venda aprovada

def test_venda_aprovada_grava_tudo(cliente, banco):
    resposta = cliente.post("/transacoes", json=venda("V1", itens=(("1", 2), ("2", 1)), total_centavos=1300))
    assert resposta.status_code == 200
    corpo = resposta.json()
    assert corpo["status"] == "aprovada" and corpo["total_centavos"] == 1300
    assert corpo["saldo_centavos"] == 100000 - 1300 == saldo(banco, BRANCO)
    assert estoque(banco, "1") == 48 and estoque(banco, "2") == 49
    itens = banco.execute("SELECT p.codigo, i.quantidade, i.preco_unitario_centavos FROM itens_transacao i "
                          "JOIN produtos p ON p.id = i.produto_id ORDER BY p.codigo").fetchall()
    assert [tuple(i) for i in itens] == [("1", 2, 500), ("2", 1, 300)]
    linha = banco.execute("SELECT status, origem, total_centavos FROM transacoes WHERE id_transacao='V1'").fetchone()
    assert tuple(linha) == ("aprovada", "online", 1300)
    assert contar(banco, "logs_auditoria", "evento = 'venda_aprovada'") == 1
    assert contar(banco, "logs_auditoria", "evento = 'saldo_alterado'") == 1      # gatilho do banco
    assert contar(banco, "logs_auditoria", "evento = 'estoque_alterado'") == 2


def test_total_e_recalculado_pelo_servidor(cliente, banco):
    corpo = cliente.post("/transacoes", json=venda("V1", itens=(("6", 1),), total_centavos=1)).json()
    assert corpo["total_centavos"] == 1200
    assert saldo(banco, BRANCO) == 100000 - 1200


def test_mesmo_produto_em_duas_linhas_e_somado(cliente, banco):
    cliente.post("/transacoes", json=venda("V1", itens=(("7", 1), ("7", 1))))
    assert estoque(banco, "7") == 0


def test_uid_em_minusculas_e_aceito(cliente):
    assert cliente.post("/transacoes", json=venda("V1", uid="532f07fe")).status_code == 200


# ---------------------------------------------------------------- idempotência

def test_venda_repetida_nao_cobra_duas_vezes(cliente, banco):
    primeira = cliente.post("/transacoes", json=venda("V1", itens=(("1", 1),))).json()
    segunda = cliente.post("/transacoes", json=venda("V1", itens=(("1", 1),)))
    assert segunda.status_code == 200
    assert segunda.json() == dict(primeira, repetida=True)
    assert saldo(banco, BRANCO) == 100000 - 500
    assert contar(banco, "transacoes") == 1


def test_recusa_repetida_devolve_a_mesma_recusa(cliente):
    primeira = cliente.post("/transacoes", json=venda("V1", uid="FFFFFFFF"))
    segunda = cliente.post("/transacoes", json=venda("V1", uid="FFFFFFFF"))
    assert primeira.status_code == segunda.status_code == 422
    assert segunda.json()["repetida"] is True


def test_idempotencia_sobrevive_ao_reinicio(tmp_path):
    from fastapi.testclient import TestClient
    from backend.app import criar_app
    caminho = str(tmp_path / "persistente.db")
    TestClient(criar_app(caminho)).post("/transacoes", json=venda("V1"))
    novo = criar_app(caminho)                         # "reinicia" o servidor
    resposta = TestClient(novo).post("/transacoes", json=venda("V1"))
    assert resposta.json()["repetida"] is True
    assert saldo(novo.state.servico._con, BRANCO) == 100000 - 500


# ---------------------------------------------------------------- recusas

@pytest.mark.parametrize("pacote, motivo", [
    (venda("R1", uid="FFFFFFFF"), "Cartão não cadastrado"),
    (venda("R2", uid=BLOQUEADO), "Cartão bloqueado"),
    (venda("R3", itens=(("9", 1),)), "Produto 9 inexistente"),
    (venda("R4", itens=(("7", 3),)), "Sem estoque de Chocolate"),
    (venda("R5", uid=CHAVEIRO, itens=(("6", 1),)), "Saldo insuficiente"),
])
def test_recusas_com_motivo(cliente, banco, pacote, motivo):
    resposta = cliente.post("/transacoes", json=pacote)
    assert resposta.status_code == 422
    assert resposta.json() == {"status": "recusada", "motivo": motivo}
    linha = banco.execute("SELECT status, motivo FROM transacoes WHERE id_transacao = ?",
                          (pacote["id_transacao"],)).fetchone()
    assert tuple(linha) == ("recusada", motivo)
    assert contar(banco, "logs_auditoria", "evento = 'venda_recusada'") == 1


def test_recusa_por_saldo_desfaz_a_baixa_de_estoque(cliente, banco):
    """Atomicidade: o estoque é baixado ANTES de conferir o saldo. Se o saldo
    não dá, o ROLLBACK devolve o estoque e apaga a auditoria dessa tentativa."""
    antes = estoque(banco, "2")
    resposta = cliente.post("/transacoes", json=venda("R1", uid=CHAVEIRO, itens=(("2", 1), ("1", 1))))
    assert resposta.json()["motivo"] == "Saldo insuficiente"
    assert estoque(banco, "2") == antes
    assert saldo(banco, CHAVEIRO) == 500
    assert contar(banco, "itens_transacao") == 0
    assert contar(banco, "logs_auditoria", "evento = 'estoque_alterado'") == 0


@pytest.mark.parametrize("mudanca, campo", [
    ({"uid_cartao": "XYZ"}, "uid_cartao"),
    ({"itens": []}, "itens"),
    ({"itens": [{"codigo": "1", "quantidade": 0}]}, "itens.0.quantidade"),
    ({"itens": [{"codigo": "1", "quantidade": "2"}]}, "itens.0.quantidade"),
    ({"itens": [{"codigo": "12", "quantidade": 1}]}, "itens.0.codigo"),
    ({"origem": "outra"}, "origem"),
    ({"terminal": ""}, "terminal"),
])
def test_pacote_invalido_400(cliente, banco, mudanca, campo):
    pacote = venda("X1")
    pacote.update(mudanca)
    resposta = cliente.post("/transacoes", json=pacote)
    assert resposta.status_code == 400
    assert resposta.json()["motivo"] == "Pacote inválido: " + campo
    assert contar(banco, "transacoes") == 0


def test_campo_faltando_e_json_quebrado(cliente):
    pacote = venda("X1")
    del pacote["itens"]
    assert cliente.post("/transacoes", json=pacote).json()["motivo"] == "Pacote inválido: itens"
    quebrado = cliente.post("/transacoes", content=b"{lixo", headers={"Content-Type": "application/json"})
    assert quebrado.status_code == 400


# ---------------------------------------------------------------- concorrência

def test_vendas_simultaneas_nunca_deixam_saldo_negativo(servico, banco):
    """20 vendas de R$ 3,00 ao mesmo tempo no chaveiro (saldo R$ 5,00):
    só UMA pode ser aprovada."""
    resultados = []

    def comprar(numero):
        resultados.append(servico.processar_venda(venda("C{}".format(numero), uid=CHAVEIRO,
                                                        itens=(("2", 1),)))[1]["status"])

    threads = [threading.Thread(target=comprar, args=(n,)) for n in range(20)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert resultados.count("aprovada") == 1 and resultados.count("recusada") == 19
    assert saldo(banco, CHAVEIRO) == 200


# ---------------------------------------------------------------- o banco se protege sozinho

def test_banco_recusa_saldo_negativo_e_altera_auditoria(cliente, banco):
    cliente.post("/transacoes", json=venda("V1"))
    with pytest.raises(sqlite3.IntegrityError):
        banco.execute("UPDATE contas SET saldo_centavos = -1 WHERE id = 1")
    with pytest.raises(sqlite3.DatabaseError, match="não podem ser alterados"):
        banco.execute("UPDATE logs_auditoria SET evento = 'apagado'")
    with pytest.raises(sqlite3.DatabaseError, match="não podem ser apagados"):
        banco.execute("DELETE FROM logs_auditoria")
    with pytest.raises(sqlite3.IntegrityError):         # id_transacao é UNIQUE
        banco.execute("INSERT INTO transacoes (id_transacao, terminal_id, uid_cartao, status, "
                      "codigo_http, resposta) VALUES ('V1', 1, 'X', 'aprovada', 200, '{}')")


# ---------------------------------------------------------------- estorno e resumo

def test_estorno_devolve_dinheiro_e_estoque(cliente, banco):
    cliente.post("/transacoes", json=venda("V1", itens=(("6", 2),)))
    resposta = cliente.post("/estornos", json={"id_transacao": "V1", "terminal": "POS-01"})
    assert resposta.status_code == 200
    assert resposta.json() == {"status": "estornada", "total_centavos": 2400, "saldo_centavos": 100000}
    assert saldo(banco, BRANCO) == 100000 and estoque(banco, "6") == 15
    assert contar(banco, "estornos") == 1
    repetido = cliente.post("/estornos", json={"id_transacao": "V1", "terminal": "POS-01"}).json()
    assert repetido["repetido"] is True and saldo(banco, BRANCO) == 100000


@pytest.mark.parametrize("pedido, codigo, motivo", [
    ({"id_transacao": "NADA", "terminal": "POS-01"}, 404, "Venda não encontrada"),
    ({"id_transacao": "V1", "terminal": "POS-09"}, 403, "Venda de outro terminal"),
    ({"id_transacao": "R1", "terminal": "POS-01"}, 409, "Venda recusada não pode ser cancelada"),
])
def test_estornos_recusados(cliente, pedido, codigo, motivo):
    cliente.post("/transacoes", json=venda("V1"))
    cliente.post("/transacoes", json=venda("R1", uid="FFFFFFFF"))
    resposta = cliente.post("/estornos", json=pedido)
    assert resposta.status_code == codigo and resposta.json()["motivo"] == motivo


def test_resumo_do_caixa(cliente):
    cliente.post("/transacoes", json=venda("V1", itens=(("1", 1),)))
    cliente.post("/transacoes", json=venda("V2", itens=(("6", 1),)))
    cliente.post("/transacoes", json=venda("R1", uid="FFFFFFFF"))
    cliente.post("/estornos", json={"id_transacao": "V2", "terminal": "POS-01"})
    assert cliente.get("/terminais/POS-01/resumo").json() == {
        "vendas": 1, "total_centavos": 500, "estornos": 1, "estornado_centavos": 1200, "recusadas": 1}


# ---------------------------------------------------------------- comandos remotos

def test_fila_de_comandos_fifo(cliente, banco):
    assert cliente.get("/terminais/POS-01/comandos").json() == {"comando": None}
    c1 = cliente.post("/terminais/POS-01/comandos", json={"tipo": "atualizar_catalogo"})
    c2 = cliente.post("/terminais/POS-01/comandos", json={"tipo": "mensagem", "texto": "Oi", "segundos": 5})
    assert c1.status_code == c2.status_code == 201
    frente = cliente.get("/terminais/POS-01/comandos").json()["comando"]
    assert frente == {"id": c1.json()["id"], "tipo": "atualizar_catalogo"}
    cliente.post("/terminais/POS-01/comandos/{}/confirmacao".format(frente["id"]), json={"resultado": "ok"})
    cliente.post("/terminais/POS-01/comandos/{}/confirmacao".format(frente["id"]), json={"resultado": "ok"})
    proximo = cliente.get("/terminais/POS-01/comandos").json()["comando"]
    assert proximo == {"id": c2.json()["id"], "tipo": "mensagem", "texto": "Oi", "segundos": 5}
    assert cliente.get("/terminais/POS-02/comandos").json() == {"comando": None}   # fila por terminal
    assert contar(banco, "logs_auditoria", "evento = 'comando_executado'") == 1


def test_bloqueio_vale_na_hora(cliente):
    cliente.post("/terminais/POS-01/comandos", json={"tipo": "bloquear", "motivo": "Manutenção"})
    online = cliente.post("/transacoes", json=venda("V1"))
    assert online.status_code == 422 and online.json()["motivo"] == "Terminal bloqueado"
    offline_antiga = cliente.post("/transacoes", json=venda("V2", origem="contingencia"))
    assert offline_antiga.status_code == 200
    cliente.post("/terminais/POS-01/comandos", json={"tipo": "desbloquear"})
    assert cliente.post("/transacoes", json=venda("V3")).status_code == 200


@pytest.mark.parametrize("comando", [
    {"tipo": "formatar"}, {"tipo": "mensagem"}, {"tipo": "mensagem", "texto": "x", "segundos": 999}])
def test_comandos_invalidos(cliente, comando):
    assert cliente.post("/terminais/POS-01/comandos", json=comando).status_code == 400


# ---------------------------------------------------------------- gerente e telemetria

def test_alterar_preco_vale_para_a_proxima_venda(cliente, banco):
    cliente.post("/transacoes", json=venda("V1", itens=(("1", 1),)))
    produto = cliente.patch("/produtos/1", json={"preco_centavos": 400, "estoque": 10}).json()
    assert produto == {"codigo": "1", "nome": "Café", "preco_centavos": 400, "estoque": 10}
    assert cliente.post("/transacoes", json=venda("V2", itens=(("1", 1),))).json()["total_centavos"] == 400
    precos = banco.execute("SELECT preco_unitario_centavos FROM itens_transacao ORDER BY id").fetchall()
    assert [p[0] for p in precos] == [500, 400]                        # histórico preservado
    assert contar(banco, "logs_auditoria", "evento = 'preco_alterado'") == 1
    assert cliente.patch("/produtos/9", json={"estoque": 1}).status_code == 404
    assert cliente.patch("/produtos/1", json={}).status_code == 400


def test_telemetria_e_terminal_novo(cliente, banco):
    dados = {"terminal": "POS-02", "online": True, "rssi": -61, "memoria_livre": 98000,
             "fila_pendente": 0, "versao": "0.4"}
    assert cliente.post("/telemetria", json=dados).json() == {"ok": True}
    terminais = {t["codigo"]: t for t in cliente.get("/terminais").json()["terminais"]}
    assert terminais["POS-02"]["ultima_telemetria"]["versao"] == "0.4"
    assert terminais["POS-02"]["ultimo_contato"] is not None
    assert contar(banco, "logs_auditoria", "evento = 'terminal_cadastrado'") == 1


def test_consultas_do_dashboard(cliente):
    cliente.post("/transacoes", json=venda("V1"))
    cliente.post("/transacoes", json=venda("R1", uid="FFFFFFFF"))
    transacoes = cliente.get("/transacoes?limite=5").json()["transacoes"]
    assert [t["id_transacao"] for t in transacoes] == ["R1", "V1"]
    assert transacoes[1]["titular"] == "Cliente do cartão branco"
    assert cliente.get("/auditoria").json()["auditoria"][0]["evento"] == "venda_recusada"
    hashes = cliente.get("/diagnostico/tabelas-hash").json()
    assert hashes["cartoes"]["elementos"] == 5 and hashes["processadas"]["elementos"] == 2
