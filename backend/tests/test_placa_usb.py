"""Aba Placa (USB) do painel: sem placa de verdade, com o mpremote e a porta serial simulados."""

import os
import subprocess
import sys
from types import SimpleNamespace

import pytest

from backend.tests.conftest import RAIZ

sys.path.insert(0, os.path.join(RAIZ, "dashboard"))

import placa_usb  # noqa: E402

VALORES = {"WIFI_SSID": "Rede do Lab", "WIFI_SENHA": 'senha "difícil"', "API_URL": "http://192.168.0.15:8000",
           "TERMINAL_ID": "POS-01", "PINO_BOTAO": 32, "PIN_OPERADOR": "2580", "TECLADO": False}


class Executor:
    """Faz o papel do subprocess.run: guarda o comando e devolve uma resposta pronta."""
    def __init__(self, codigo=0, saida="", erro=""):
        self.chamadas = []
        self.resposta = SimpleNamespace(returncode=codigo, stdout=saida, stderr=erro)
        self.arquivo_enviado = None

    def __call__(self, argumentos, tempo_limite):
        self.chamadas.append(argumentos)
        if "cp" in argumentos and argumentos[-3] == ":config.py":
            with open(argumentos[-4], encoding="utf-8") as arquivo:
                self.arquivo_enviado = arquivo.read()
        return self.resposta


def test_encontra_so_placas_esp32():
    portas = [SimpleNamespace(device="COM5", vid=0x1A86, description="USB-SERIAL CH340"),
              SimpleNamespace(device="COM1", vid=None, description="Porta de comunicação"),
              SimpleNamespace(device="/dev/cu.usbserial-0001", vid=0x10C4, description="CP2102")]
    assert [p["porta"] for p in placa_usb.listar_placas(portas)] == ["/dev/cu.usbserial-0001", "COM5"]
    assert placa_usb.listar_placas(portas)[1]["chip"] == "CH340 (WCH)"


def test_endereco_do_backend_para_a_placa():
    assert placa_usb.api_url_para_a_placa("http://127.0.0.1:8000", ip="192.168.0.15") == "http://192.168.0.15:8000"
    assert placa_usb.api_url_para_a_placa("http://localhost:8080/", ip="10.0.0.2") == "http://10.0.0.2:8080"
    assert placa_usb.api_url_para_a_placa("http://192.168.1.7:8000") == "http://192.168.1.7:8000"


def test_config_ida_e_volta_sem_executar_nada():
    texto = placa_usb.gerar_config(VALORES)
    assert placa_usb.ler_config(texto) == VALORES                # aspas e acento escapados com repr()
    malicioso = 'import os\nos.remove("x")\nWIFI_SSID = __import__("os").getcwd()\nAPI_URL = "http://1.2.3.4:8000"\n'
    assert placa_usb.ler_config(malicioso) == {"API_URL": "http://1.2.3.4:8000"}   # só valores simples
    assert placa_usb.ler_config("isto não é python (") == {}


@pytest.mark.parametrize("campo, valor", [("WIFI_SSID", ""), ("WIFI_SENHA", "curta"),
                                          ("API_URL", "192.168.0.15:8000"), ("TERMINAL_ID", "POS 01"),
                                          ("PINO_BOTAO", 5), ("PIN_OPERADOR", "12a4")])
def test_validacao(campo, valor):
    assert placa_usb.validar_config(dict(VALORES, **{campo: valor}))
    assert placa_usb.validar_config(VALORES) == []


def test_gravar_config_e_enviar_codigo():
    executar = Executor()
    placa = placa_usb.Placa("COM5", executar=executar)
    placa.gravar_config(VALORES)
    comando = executar.chamadas[0]
    assert comando[1:5] == ["-m", "mpremote", "connect", "COM5"]
    assert comando[-4:-1] == [comando[-4], ":config.py", "+"] and comando[-1] == "reset"
    assert "WIFI_SENHA = 'senha \"difícil\"'" in executar.arquivo_enviado
    enviados = placa.enviar_codigo()
    nomes = [os.path.basename(caminho) for caminho in executar.chamadas[1] if caminho.endswith(".py")]
    assert enviados == len(nomes) and "main.py" in nomes and "boot.py" in nomes
    assert "config.py" not in nomes and "config_exemplo.py" not in nomes


def test_erros_viram_mensagens_claras():
    ocupada = placa_usb.Placa("COM5", executar=Executor(1, erro="failed to access COM5 (it may be in use by another program)"))
    with pytest.raises(placa_usb.ErroPlaca, match="Feche o Thonny"):
        ocupada.ler_config()
    sem_config = placa_usb.Placa("COM5", executar=Executor(1, erro="OSError: [Errno 2] ENOENT"))
    assert sem_config.ler_config() == {}

    def demora(argumentos, tempo_limite):
        raise subprocess.TimeoutExpired(argumentos, tempo_limite)
    with pytest.raises(placa_usb.ErroPlaca, match="não respondeu"):
        placa_usb.Placa("COM5", executar=demora).enviar_codigo()


class SerialFalsa:
    def __init__(self, saida):
        self.escrito = b""
        self._saida = saida

    def write(self, dados):
        self.escrito += dados

    def reset_input_buffer(self):
        pass

    def read(self, tamanho):
        pedaco, self._saida = self._saida[:tamanho], self._saida[tamanho:]
        return pedaco

    def close(self):
        pass


def test_console_reinicia_e_resume():
    log = (b"MPY: soft reboot\r\n[Wi-Fi] Conectado. IP: 192.168.0.40\r\n"
           b"[CATALOGO] 7 produto(s) recebidos do servidor.\r\n[MAIN] Terminal pronto. Fila offline: 0 pendente(s).\r\n")
    serial = SerialFalsa(log)
    texto = placa_usb.Placa("COM5", abrir_serial=lambda: serial).console(segundos=0.3)
    assert serial.escrito == b"\r\x03\x03\x04"                      # Ctrl+C, Ctrl+C e Ctrl+D (soft reset)
    assert texto.splitlines()[1] == "[Wi-Fi] Conectado. IP: 192.168.0.40"
    assert [nivel for nivel, _ in placa_usb.resumo_do_console(texto)] == ["bom", "bom", "bom"]
    falhou = placa_usb.resumo_do_console("[Wi-Fi] Não foi possível conectar após 3 tentativa(s).\n"
                                         "[CATALOGO] Usando o catálogo padrão (7 produtos).")
    assert falhou == [("critico", "Wi-Fi não conectou: confira nome, senha e se a rede é de 2,4 GHz")]


def test_aba_sem_placa_no_painel(monkeypatch):
    """O painel abre a aba sem erro quando não há placa (o caso deste computador de testes)."""
    pytest.importorskip("streamlit")
    from streamlit.testing.v1 import AppTest
    from backend.tests.test_esp32_contra_backend import porta_livre  # noqa: F401
    import threading
    import time
    import uvicorn
    from backend.app import criar_app
    import tempfile
    pasta = tempfile.mkdtemp()
    porta = porta_livre()
    servidor = uvicorn.Server(uvicorn.Config(criar_app(os.path.join(pasta, "b.db")), host="127.0.0.1",
                                             port=porta, log_level="warning"))
    threading.Thread(target=servidor.run, daemon=True).start()
    while not servidor.started:
        time.sleep(0.05)
    monkeypatch.setenv("BYTEBANK_API_URL", "http://127.0.0.1:{}".format(porta))
    monkeypatch.setattr(placa_usb, "listar_placas", lambda portas=None: [])
    app = AppTest.from_file(os.path.join(RAIZ, "dashboard", "app.py"), default_timeout=30).run()
    servidor.should_exit = True
    assert not app.exception
    assert "Placa ligada no USB deste computador" in [s.value for s in app.subheader]
    assert any("Nenhuma placa encontrada" in i.value for i in app.info)
