"""Reposição de estoque sem atualização perdida (lost update).

O problema: o gerente abre o painel e vê 50 Cafés. Enquanto ele digita,
o caixa vende 1 (sobram 49). Se ele salvar "70" pensando em "50 + 20",
a venda some da conta: o certo era 69.

As duas defesas:
  * reposição SOMA no banco (estoque = estoque + 20), sem ler-alterar-gravar;
  * correção de contagem manda o valor que o gerente viu (estoque_esperado)
    e o UPDATE só acontece se ele ainda for o mesmo (compare-and-set).
"""

import threading

from backend.tests.conftest import venda


def estoque(banco, codigo):
    return banco.execute("SELECT estoque FROM produtos WHERE codigo = ?", (codigo,)).fetchone()[0]


def test_reposicao_soma_e_nao_apaga_a_venda_do_meio(cliente, banco):
    visto_pelo_gerente = cliente.get("/produtos").json()["produtos"][0]["estoque"]
    assert visto_pelo_gerente == 50
    cliente.post("/transacoes", json=venda("V1", itens=(("1", 1),)))      # venda enquanto ele digita
    produto = cliente.post("/produtos/1/reposicao", json={"unidades": 20}).json()
    assert produto == {"codigo": "1", "nome": "Café", "preco_centavos": 500, "estoque": 69}
    assert estoque(banco, "1") == 69
    log = banco.execute("SELECT valor_anterior, valor_novo FROM logs_auditoria "
                        "WHERE evento = 'estoque_alterado' ORDER BY id DESC").fetchone()
    assert tuple(log) == (49, 69)


def test_correcao_com_valor_desatualizado_e_recusada(cliente, banco):
    cliente.post("/transacoes", json=venda("V1", itens=(("1", 1),)))      # 50 -> 49
    resposta = cliente.patch("/produtos/1", json={"estoque": 45, "estoque_esperado": 50,
                                                   "preco_centavos": 400})
    assert resposta.status_code == 409
    assert resposta.json()["motivo"].startswith("O estoque mudou de 50 para 49")
    # nada foi gravado: nem o estoque, nem o preço que veio junto (ROLLBACK)
    assert estoque(banco, "1") == 49
    assert cliente.get("/produtos").json()["produtos"][0]["preco_centavos"] == 500
    # com o valor certo, passa
    assert cliente.patch("/produtos/1", json={"estoque": 45, "estoque_esperado": 49}).json()["estoque"] == 45


def test_reposicao_invalida(cliente):
    assert cliente.post("/produtos/1/reposicao", json={"unidades": 0}).status_code == 400
    assert cliente.post("/produtos/1/reposicao", json={"unidades": "10"}).status_code == 400
    assert cliente.post("/produtos/9/reposicao", json={"unidades": 5}).status_code == 404


def test_reposicoes_e_vendas_ao_mesmo_tempo(servico, banco):
    """10 reposições de 1 unidade e 10 vendas de 1 Café, todas ao mesmo tempo:
    o estoque final tem que fechar a conta (50 + 10 - 10)."""
    def repor():
        servico.repor_estoque("1", 1)

    def vender(numero):
        servico.processar_venda(venda("S{}".format(numero), itens=(("1", 1),),
                                      uid="A1B2C3D4" if numero % 2 else "532F07FE"))

    servico.antifraude.avaliar = lambda *argumentos, **nomeados: None    # aqui o assunto é estoque
    threads = [threading.Thread(target=repor) for _ in range(10)]
    threads += [threading.Thread(target=vender, args=(n,)) for n in range(10)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    vendidas = banco.execute("SELECT COUNT(*) FROM transacoes WHERE status = 'aprovada'").fetchone()[0]
    assert vendidas == 10
    assert estoque(banco, "1") == 50
