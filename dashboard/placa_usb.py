"""
placa_usb.py - Conversa com a placa pelo cabo USB (o que o Thonny faz),
para a aba "Placa (USB)" do painel.

    listar_placas()          placas ESP32 ligadas no USB deste computador
    Placa(porta).ler_config()     lê o config.py da placa (sem a senha do Wi-Fi na tela)
    Placa(porta).gravar_config()  grava um config.py novo e reinicia a placa
    Placa(porta).enviar_codigo()  copia os .py da pasta esp32/ e reinicia
    Placa(porta).console()        reinicia e devolve o que a placa escreveu (o "Shell" do Thonny)

Usa o mpremote, a ferramenta oficial do MicroPython para isso (pip install
mpremote), e o pyserial, que vem junto com ele.

Limites:
  * só funciona no computador onde a placa está PLUGADA (o painel aberto
    de outro computador da rede não alcança o USB);
  * uma porta USB só pode ser usada por UM programa de cada vez: com o
    Thonny aberto e conectado, a placa aparece como ocupada.

O config.py lido da placa é interpretado com ast.literal_eval (só aceita
valores simples, como textos e números): nada do arquivo é executado no
computador.
"""

import ast
import os
import re
import socket
import subprocess
import sys
import tempfile
import time
import urllib.parse

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PASTA_ESP32 = os.path.join(RAIZ, "esp32")

# Chips USB-serial das placas ESP32 (identificador do fabricante, VID)
CHIPS = {
    0x10C4: "CP210x (Silicon Labs)",
    0x1A86: "CH340 (WCH)",
    0x0403: "FTDI",
    0x303A: "Espressif (USB nativo)",
}

CAMPOS = ("WIFI_SSID", "WIFI_SENHA", "API_URL", "TERMINAL_ID", "PINO_BOTAO", "PIN_OPERADOR")
NAO_ENVIAR = {"config.py", "config_exemplo.py"}     # o config.py é gravado pelo formulário

AVISO_PORTA_OCUPADA = ("A porta USB está ocupada por outro programa. Feche o Thonny (ou clique em "
                       "Stop/Desconectar nele) e tente de novo.")


class ErroPlaca(Exception):
    pass


# ---------------------------------------------------------------- encontrar a placa

def listar_placas(portas=None):
    """[{porta, descricao, chip}] das portas com um chip conhecido de ESP32."""
    if portas is None:
        from serial.tools import list_ports
        portas = list_ports.comports()
    placas = []
    for porta in portas:
        if porta.vid in CHIPS:
            placas.append({"porta": porta.device, "chip": CHIPS[porta.vid],
                           "descricao": porta.description or porta.device})
    return sorted(placas, key=lambda placa: placa["porta"])


def mpremote_instalado():
    try:
        import mpremote  # noqa: F401
        import serial  # noqa: F401
        return True
    except ImportError:
        return False


# ---------------------------------------------------------------- endereço do backend para a placa

def ip_local():
    """IP deste computador na rede (o mesmo truque do backend: não envia nada)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


def api_url_para_a_placa(url_do_painel, ip=None):
    """O painel fala com o backend em 127.0.0.1, mas a placa precisa do IP
    deste computador NA REDE. Troca localhost pelo IP e mantém a porta."""
    partes = urllib.parse.urlsplit(url_do_painel.strip())
    host = partes.hostname or "127.0.0.1"
    if host in ("127.0.0.1", "localhost", "::1", "0.0.0.0"):
        host = ip or ip_local()
    porta = partes.port or 8000
    return "http://{}:{}".format(host, porta)


# ---------------------------------------------------------------- config.py

def ler_config(texto):
    """Valores do config.py, sem executar o arquivo (ast.literal_eval)."""
    valores = {}
    try:
        arvore = ast.parse(texto)
    except SyntaxError:
        return valores
    for no in arvore.body:
        if (isinstance(no, ast.Assign) and len(no.targets) == 1 and isinstance(no.targets[0], ast.Name)
                and no.targets[0].id in CAMPOS):
            try:
                valores[no.targets[0].id] = ast.literal_eval(no.value)
            except ValueError:
                pass
    return valores


def validar_config(valores):
    """Lista de problemas (vazia = pode gravar)."""
    problemas = []
    ssid = valores.get("WIFI_SSID") or ""
    senha = valores.get("WIFI_SENHA") or ""
    if not ssid or len(ssid) > 32:
        problemas.append("Nome da rede: de 1 a 32 caracteres.")
    if senha and not 8 <= len(senha) <= 63:
        problemas.append("Senha do Wi-Fi: de 8 a 63 caracteres (ou vazia, para rede aberta).")
    if not re.match(r"^http://[^/\s]+:\d+$", valores.get("API_URL") or ""):
        problemas.append("Endereço do backend: no formato http://192.168.0.10:8000.")
    if not re.match(r"^[A-Za-z0-9_-]{1,32}$", valores.get("TERMINAL_ID") or ""):
        problemas.append("Terminal: letras, números, - ou _ (ex.: POS-01).")
    if valores.get("PINO_BOTAO") not in (0, 32):
        problemas.append("Pino do botão: 32 (botão no protoboard) ou 0 (botão BOOT).")
    pin = valores.get("PIN_OPERADOR")
    if pin and not (isinstance(pin, str) and pin.isdigit() and 4 <= len(pin) <= 8):
        problemas.append("PIN do operador: de 4 a 8 dígitos.")
    return problemas


def gerar_config(valores):
    """Texto do config.py, no mesmo formato do config_exemplo.py.
    Os valores entram com repr(): aspas e acentos ficam escapados."""
    linhas = [
        '"""',
        "config.py - Configuração deste terminal (gravado pelo painel, aba Placa (USB))",
        "",
        "Não vai para o GitHub (.gitignore): tem a senha do Wi-Fi.",
        '"""',
        "",
        "# Rede Wi-Fi (o ESP32 só conecta em redes de 2,4 GHz)",
        "WIFI_SSID = {!r}".format(valores["WIFI_SSID"]),
        "WIFI_SENHA = {!r}".format(valores.get("WIFI_SENHA") or ""),
        "",
        "# Endereço do backend (o IP do computador que roda  python -m backend)",
        "API_URL = {!r}".format(valores["API_URL"]),
        "",
        "# Identificação deste terminal no sistema",
        "TERMINAL_ID = {!r}".format(valores["TERMINAL_ID"]),
        "",
        "# Pino do botão: 32 = botão no protoboard; 0 = botão BOOT da placa",
        "PINO_BOTAO = {!r}".format(int(valores["PINO_BOTAO"])),
    ]
    if valores.get("PIN_OPERADOR"):
        linhas += ["", "# PIN do modo operador (carrinho vazio e segurar o botão 5 s)",
                   "PIN_OPERADOR = {!r}".format(valores["PIN_OPERADOR"])]
    return "\n".join(linhas) + "\n"


def arquivos_da_placa(pasta=PASTA_ESP32):
    """Os .py que rodam na placa (sem os exemplos e sem o config.py)."""
    return sorted(os.path.join(pasta, nome) for nome in os.listdir(pasta)
                  if nome.endswith(".py") and nome not in NAO_ENVIAR)


# ---------------------------------------------------------------- a placa

def _rodar(argumentos, tempo_limite):
    return subprocess.run(argumentos, capture_output=True, text=True, timeout=tempo_limite)


class Placa:
    def __init__(self, porta, executar=_rodar, abrir_serial=None):
        self.porta = porta
        self._executar = executar
        self._abrir_serial = abrir_serial or self._serial_de_verdade

    def _mpremote(self, *comandos, tempo_limite=60):
        argumentos = [sys.executable, "-m", "mpremote", "connect", self.porta] + list(comandos)
        try:
            resultado = self._executar(argumentos, tempo_limite)
        except subprocess.TimeoutExpired:
            raise ErroPlaca("A placa não respondeu em {} s. Desligue e ligue o cabo USB e tente de novo."
                            .format(tempo_limite))
        saida = (resultado.stdout or "") + (resultado.stderr or "")
        if resultado.returncode != 0:
            raise ErroPlaca(traduzir_erro(saida))
        return resultado.stdout or ""

    def ler_config(self):
        """Valores do config.py da placa ({} se ela ainda não tem um)."""
        try:
            return ler_config(self._mpremote("fs", "cat", ":config.py"))
        except ErroPlaca as erro:
            if "não existe" in str(erro):
                return {}
            raise

    def gravar_config(self, valores):
        problemas = validar_config(valores)
        if problemas:
            raise ErroPlaca(" ".join(problemas))
        with tempfile.TemporaryDirectory() as pasta:
            caminho = os.path.join(pasta, "config.py")
            with open(caminho, "w", encoding="utf-8") as arquivo:
                arquivo.write(gerar_config(valores))
            self._mpremote("fs", "cp", caminho, ":config.py", "+", "reset")

    def enviar_codigo(self, arquivos=None):
        arquivos = arquivos or arquivos_da_placa()
        self._mpremote("fs", "cp", *arquivos, ":", "+", "reset", tempo_limite=180)
        return len(arquivos)

    def console(self, segundos=15, reiniciar=True):
        """O que a placa escreve no console (o "Shell" do Thonny).

        reiniciar=True: interrompe o programa (Ctrl+C) e faz um soft reset
        (Ctrl+D), que roda o boot.py e o main.py de novo do começo.
        """
        try:
            conexao = self._abrir_serial()
        except Exception as erro:          # noqa: BLE001  (pyserial muda as classes de erro por sistema)
            raise ErroPlaca(traduzir_erro(str(erro)))
        try:
            if reiniciar:
                conexao.write(b"\r\x03\x03")
                time.sleep(0.3)
                conexao.reset_input_buffer()
                conexao.write(b"\x04")
            recebido = b""
            fim = time.monotonic() + segundos
            while time.monotonic() < fim:
                recebido += conexao.read(256)
        finally:
            conexao.close()
        texto = recebido.decode("utf-8", errors="replace").replace("\r\n", "\n").replace("\r", "\n")
        return texto.replace("\x04", "").strip()

    def _serial_de_verdade(self):
        import serial
        conexao = serial.Serial()
        conexao.port = self.porta
        conexao.baudrate = 115200
        conexao.timeout = 0.2
        # DTR e RTS desligados ANTES de abrir: nas placas ESP32 esses sinais
        # ligam no EN e no GPIO0, e abrir a porta com eles ativos deixa a
        # placa presa em reset ou no modo de gravação.
        conexao.dtr = False
        conexao.rts = False
        conexao.open()
        return conexao


def traduzir_erro(saida):
    texto = saida.lower()
    if any(pedaco in texto for pedaco in ("resource busy", "permission denied", "access is denied",
                                          "acesso negado", "could not exclusively lock", "failed to access",
                                          "could not open port")):
        return AVISO_PORTA_OCUPADA
    if "could not enter raw repl" in texto:
        return ("A placa não entrou no modo de comandos. Aperte o botão EN/RST da placa (ou feche o "
                "Thonny) e tente de novo.")
    if "enoent" in texto or "no such file" in texto:
        return "O arquivo não existe na placa."
    if "no device found" in texto or "could not find" in texto:
        return "Nenhuma placa encontrada nessa porta. Confira o cabo USB."
    ultima = [linha for linha in saida.strip().splitlines() if linha.strip()]
    return "Erro do mpremote: {}".format(ultima[-1] if ultima else "sem detalhes")


def resumo_do_console(texto):
    """O que deu certo e o que falhou, a partir das mensagens da placa."""
    verificacoes = [
        (None, None, "[BOOT] config.py não encontrado",
         "A placa não tem config.py: grave pela seção Configurar"),
        ("[Wi-Fi] Conectado", "Wi-Fi conectado", "[Wi-Fi] Não foi possível conectar",
         "Wi-Fi não conectou: confira nome, senha e se a rede é de 2,4 GHz"),
        ("recebidos do servidor", "Backend respondeu: catálogo recebido", "Servidor não enviou o catálogo",
         "Backend não respondeu: confira o endereço e o firewall do computador"),
        ("Terminal pronto", "Terminal pronto para vender", None, None),
    ]
    linhas = []
    for sucesso, texto_ok, falha, texto_falha in verificacoes:
        if sucesso and sucesso in texto:
            linhas.append(("bom", texto_ok))
        elif falha and falha in texto:
            linhas.append(("critico", texto_falha))
    return linhas
