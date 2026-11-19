"""Teclado 4x4 (esp32/hardware.py): varredura, debounce e as teclas no terminal,
com um 'machine.Pin' falso que simula a grade de 4 linhas x 4 colunas."""

import os
import sys
import types

import pytest

from backend.tests.conftest import RAIZ


class Grade:
    """O teclado físico: quais teclas estão apertadas (linha, coluna)."""
    def __init__(self):
        self.apertadas = set()
        self.pinos = {}


GRADE = Grade()


class PinFalso:
    IN, OUT, PULL_UP = "in", "out", "pull_up"

    def __init__(self, numero, modo=IN, pull=None, value=1):
        self.numero, self.modo, self.pull, self.valor = numero, modo, pull, value
        GRADE.pinos[numero] = self

    def init(self, modo, value=1):
        self.modo, self.valor = modo, value

    def value(self, novo=None):
        if novo is not None:
            self.valor = novo
            return None
        import hardware
        if self.numero in hardware.TECLADO_COLUNAS:             # coluna: 0 se ligada a uma linha em 0
            coluna = hardware.TECLADO_COLUNAS.index(self.numero)
            for linha_indice, numero_linha in enumerate(hardware.TECLADO_LINHAS):
                linha = GRADE.pinos[numero_linha]
                if linha.modo == PinFalso.OUT and linha.valor == 0 and (linha_indice, coluna) in GRADE.apertadas:
                    return 0
            return 1
        return self.valor


@pytest.fixture
def hardware(monkeypatch):
    maquina = types.ModuleType("machine")
    maquina.Pin = PinFalso
    maquina.SoftI2C = object
    monkeypatch.setitem(sys.modules, "machine", maquina)
    relogio = {"ms": 0}
    monkeypatch.setattr("time.ticks_ms", lambda: relogio["ms"], raising=False)
    monkeypatch.setattr("time.ticks_diff", lambda a, b: a - b, raising=False)
    sys.path.insert(0, os.path.join(RAIZ, "esp32"))
    sys.modules.pop("hardware", None)
    import hardware as modulo
    GRADE.apertadas.clear()
    modulo.relogio = relogio
    yield modulo
    sys.modules.pop("hardware", None)


def apertar(teclado, hardware, tecla, durante_ms=60):
    """Aperta a tecla por um tempo e solta; devolve o que ler() entregou."""
    for indice, linha in enumerate(hardware.TECLAS):
        if tecla in linha:
            GRADE.apertadas = {(indice, linha.index(tecla))}
    entregues = []
    for _ in range(durante_ms // 10):
        hardware.relogio["ms"] += 10
        entregues.append(teclado.ler())
    GRADE.apertadas = set()
    for _ in range(6):
        hardware.relogio["ms"] += 10
        entregues.append(teclado.ler())
    return [e for e in entregues if e is not None]


def test_cada_tecla_sai_uma_vez(hardware):
    teclado = hardware.Teclado()
    for tecla in "1234567890*#":
        assert apertar(teclado, hardware, tecla) == [tecla]            # segurar não repete
    assert apertar(teclado, hardware, "A") == [hardware.TOQUE]
    assert apertar(teclado, hardware, "B") == [hardware.SEGURAR]
    assert apertar(teclado, hardware, "C") == [hardware.SEGURAR_LONGO]
    assert apertar(teclado, hardware, "D") == []


def test_trepidacao_curta_e_ignorada(hardware):
    teclado = hardware.Teclado()
    GRADE.apertadas = {(0, 0)}
    hardware.relogio["ms"] += 10
    assert teclado.ler() is None                      # 10 ms: ainda não firmou
    GRADE.apertadas = set()
    for _ in range(5):
        hardware.relogio["ms"] += 10
        assert teclado.ler() is None


def test_linhas_ficam_soltas_depois_da_varredura(hardware):
    teclado = hardware.Teclado()
    apertar(teclado, hardware, "5")
    assert all(GRADE.pinos[n].modo == PinFalso.IN for n in hardware.TECLADO_LINHAS)
    assert all(GRADE.pinos[n].pull == PinFalso.PULL_UP for n in hardware.TECLADO_COLUNAS)


def test_pinos_nao_batem_com_o_resto_da_placa(hardware):
    pinos = set(hardware.TECLADO_LINHAS + hardware.TECLADO_COLUNAS)
    ocupados = {5, 4, hardware.RFID_SCK, hardware.RFID_MOSI, hardware.RFID_MISO, hardware.RFID_RST,
                hardware.RFID_SDA, hardware.PINO_BUZZER, hardware.PINO_LED_APROVADO, 16, 0}
    assert len(pinos) == 8 and not pinos & ocupados
    assert not pinos & {34, 35, 36, 39}               # só entrada e sem pull-up: não servem
