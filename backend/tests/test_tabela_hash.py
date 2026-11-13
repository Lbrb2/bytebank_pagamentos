"""Testes da tabela hash (backend/tabela_hash.py)."""

import random

from backend.tabela_hash import TabelaHash, fnv1a


def test_inserir_buscar_e_atualizar():
    tabela = TabelaHash()
    tabela.inserir("532F07FE", "cartão branco")
    tabela.inserir("93892B4F", "chaveiro azul")
    assert tabela.buscar("532F07FE") == "cartão branco"
    assert tabela.buscar("NAOEXISTE") is None
    assert tabela.buscar("NAOEXISTE", "padrão") == "padrão"
    tabela.inserir("532F07FE", "novo valor")              # mesma chave: troca o valor
    assert tabela.buscar("532F07FE") == "novo valor"
    assert len(tabela) == 2


def test_remover_e_contains():
    tabela = TabelaHash()
    tabela.inserir("1", "Café")
    assert "1" in tabela
    assert tabela.remover("1") is True
    assert "1" not in tabela
    assert tabela.remover("1") is False
    assert len(tabela) == 0


def test_valor_none_e_diferente_de_ausente():
    tabela = TabelaHash()
    tabela.inserir("chave", None)
    assert "chave" in tabela


def test_colisoes_no_mesmo_balde():
    tabela = TabelaHash(capacidade=1)    # um balde só: tudo colide
    tabela.FATOR_DE_CARGA_MAXIMO = 100   # impede o redimensionamento neste teste
    for numero in range(20):
        tabela.inserir("chave{}".format(numero), numero)
    assert tabela.estatisticas()["maior_balde"] == 20
    assert all(tabela.buscar("chave{}".format(n)) == n for n in range(20))


def test_redimensiona_e_mantem_fator_de_carga():
    tabela = TabelaHash()
    for numero in range(1000):
        tabela.inserir("uid{:08X}".format(numero), numero)
    estatisticas = tabela.estatisticas()
    assert estatisticas["elementos"] == 1000
    assert estatisticas["fator_de_carga"] <= TabelaHash.FATOR_DE_CARGA_MAXIMO
    assert estatisticas["baldes"] >= 1000 / TabelaHash.FATOR_DE_CARGA_MAXIMO
    assert estatisticas["maior_balde"] <= 8       # chaves bem espalhadas
    assert all(tabela.buscar("uid{:08X}".format(n)) == n for n in range(1000))


def test_igual_a_um_dict_em_operacoes_aleatorias():
    sorteio = random.Random(42)
    tabela, referencia = TabelaHash(), {}
    for _ in range(5000):
        chave = "k{}".format(sorteio.randint(0, 300))
        operacao = sorteio.random()
        if operacao < 0.6:
            tabela.inserir(chave, operacao)
            referencia[chave] = operacao
        elif operacao < 0.8:
            assert tabela.remover(chave) == (referencia.pop(chave, None) is not None)
        else:
            assert tabela.buscar(chave) == referencia.get(chave)
    assert len(tabela) == len(referencia)
    assert dict(tabela.itens()) == referencia


def test_fnv1a_valores_conhecidos():
    # Valores de referência do FNV-1a 32 bits
    assert fnv1a("") == 0x811C9DC5
    assert fnv1a("a") == 0xE40C292C
    assert fnv1a("foobar") == 0xBF9CF968
