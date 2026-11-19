"""Senha do cartão (extra): hash PBKDF2, compras a partir de R$ 20,00 e bloqueio após 3 erros."""

from backend import senhas
from backend.tests.conftest import venda

DOIS_SANDUICHES = (("6", 2),)        # R$ 24,00: pede senha
UM_CAFE = (("1", 1),)                # R$ 5,00: aproximação, sem senha


def comprar(cliente, id_transacao, senha=None, itens=DOIS_SANDUICHES, **extras):
    if senha is not None:
        extras["senha"] = senha
    return cliente.post("/transacoes", json=venda(id_transacao, itens=itens, **extras))


def cartao(banco, uid="532F07FE"):
    return banco.execute("SELECT status, motivo_bloqueio, pin_hash, pin_erros FROM cartoes "
                         "WHERE uid_rfid = ?", (uid,)).fetchone()


# ---------------------------------------------------------------- hash

def test_hash_nunca_guarda_a_senha():
    guardado = senhas.gerar_hash("1234")
    algoritmo, iteracoes, salt, digest = guardado.split("$")
    assert algoritmo == "pbkdf2_sha256" and int(iteracoes) == 100_000 and len(salt) == 32
    assert "1234" not in guardado
    assert senhas.conferir("1234", guardado) and not senhas.conferir("1235", guardado)
    assert senhas.gerar_hash("1234") != guardado                 # salt novo a cada vez
    assert not senhas.conferir("1234", "lixo") and not senhas.conferir("1234", None)


def test_banco_so_tem_hash(banco):
    for linha in banco.execute("SELECT pin_hash FROM cartoes"):
        assert linha[0].startswith("pbkdf2_sha256$")
    assert senhas.conferir("1234", cartao(banco)["pin_hash"])


# ---------------------------------------------------------------- regra

def test_ate_20_reais_e_aproximacao_sem_senha(cliente):
    assert comprar(cliente, "V1", itens=UM_CAFE).json()["status"] == "aprovada"


def test_acima_de_20_reais_pede_senha(cliente, banco):
    sem = comprar(cliente, "V1")
    assert sem.status_code == 422 and sem.json()["motivo"] == "Senha obrigatória acima de R$ 20,00"
    certa = comprar(cliente, "V2", senha="1234")
    assert certa.json()["status"] == "aprovada" and certa.json()["total_centavos"] == 2400
    resposta = banco.execute("SELECT resposta FROM transacoes WHERE id_transacao = 'V2'").fetchone()[0]
    assert "1234" not in resposta                                 # a senha não é gravada


def test_tres_senhas_erradas_bloqueiam(cliente, banco):
    for numero in range(2):
        resposta = comprar(cliente, "E{}".format(numero), senha="9999")
        assert resposta.json()["motivo"] == "Senha incorreta"
    assert cartao(banco)["pin_erros"] == 2 and cartao(banco)["status"] == "ativo"
    terceira = comprar(cliente, "E2", senha="9999")
    assert terceira.json() == {"status": "recusada", "motivo": "Senha incorreta", "detalhe": "Cartão bloqueado"}
    assert tuple(cartao(banco))[:2] == ("bloqueado", "Senha incorreta 3 vezes seguidas")
    # nada foi cobrado, e agora nem a senha certa passa
    assert banco.execute("SELECT saldo_centavos FROM contas WHERE id = 1").fetchone()[0] == 100000
    assert comprar(cliente, "C1", senha="1234").json()["motivo"] == "Cartão bloqueado por segurança"
    assert comprar(cliente, "C2", itens=UM_CAFE).json()["motivo"] == "Cartão bloqueado por segurança"
    eventos = [linha[0] for linha in banco.execute(
        "SELECT evento FROM logs_auditoria WHERE evento LIKE 'senha_%' ORDER BY id")]
    assert eventos == ["senha_incorreta", "senha_incorreta", "senha_bloqueio"]
    # o painel vê o bloqueio e o gerente libera; o contador volta a zero
    bloqueados = {c["uid_rfid"]: c for c in cliente.get("/cartoes/bloqueados").json()["cartoes"]}
    assert bloqueados["532F07FE"]["motivo_bloqueio"] == "Senha incorreta 3 vezes seguidas"
    cliente.post("/cartoes/532F07FE/desbloquear")
    assert cartao(banco)["pin_erros"] == 0
    assert comprar(cliente, "C3", senha="1234").json()["status"] == "aprovada"


def test_senha_certa_zera_os_erros(cliente, banco):
    comprar(cliente, "E1", senha="0000")
    comprar(cliente, "E2", senha="0000")
    assert comprar(cliente, "V1", senha="1234").json()["status"] == "aprovada"
    assert cartao(banco)["pin_erros"] == 0
    comprar(cliente, "E3", senha="0000")
    assert cartao(banco)["status"] == "ativo"                    # erros SEGUIDOS: recomeçou do zero


def test_fila_offline_nao_pede_senha(cliente):
    resposta = comprar(cliente, "F1", origem="contingencia")
    assert resposta.json()["status"] == "aprovada"


def test_senha_fora_do_formato(cliente):
    for senha in ("123", "12345", "abcd"):
        assert comprar(cliente, "X" + senha, senha=senha).status_code == 400


def test_cartao_sem_senha_cadastrada(cliente, banco):
    banco.execute("UPDATE cartoes SET pin_hash = NULL WHERE uid_rfid = '532F07FE'")
    from backend.app import criar_app                            # recarrega as tabelas hash
    from fastapi.testclient import TestClient
    novo = TestClient(criar_app(banco.execute("PRAGMA database_list").fetchone()[2]))
    assert comprar(novo, "V1").json()["status"] == "aprovada"


def test_saldo_insuficiente_continua_valendo_com_senha(cliente):
    resposta = comprar(cliente, "V1", senha="4321", uid="93892B4F")      # chaveiro: R$ 5,00
    assert resposta.json()["motivo"] == "Saldo insuficiente"


def test_banco_da_entrega_3_e_atualizado(tmp_path):
    """Um banco criado com o schema da Entrega 3 ganha as colunas novas,
    sem perder dados; os cartões antigos ficam sem senha (não pedem)."""
    import os
    import sqlite3
    from backend import banco as modulo_banco
    pasta = os.path.join(os.path.dirname(__file__), "dados")
    caminho = str(tmp_path / "entrega3.db")
    antigo = sqlite3.connect(caminho)
    for arquivo in ("schema_entrega3.sql", "seed_entrega3.sql"):
        with open(os.path.join(pasta, arquivo), encoding="utf-8") as sql:
            antigo.executescript(sql.read())
    antigo.close()
    conexao = modulo_banco.conectar(caminho)
    colunas = {linha["name"] for linha in conexao.execute("PRAGMA table_info(cartoes)")}
    assert {"motivo_bloqueio", "bloqueado_em", "pin_hash", "pin_erros"} <= colunas
    assert conexao.execute("SELECT COUNT(*), MAX(pin_hash), SUM(pin_erros) FROM cartoes").fetchone()[:] == (5, None, 0)
