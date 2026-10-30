"""
simulador.py - Roda o terminal ByteBank Pay no computador, sem a placa

Usa os MESMOS arquivos da pasta esp32/ (terminal, carrinho, catálogo, fila,
pagamentos, api). Só o hardware é trocado: o display vira texto no console,
o botão vira o que você digitar, e o cartão é "aproximado" por comando.

Útil para testar o fluxo de venda e a contingência offline antes de montar
o circuito, e para demonstrar o funcionamento.

Uso (com o servidor de teste rodando em outro terminal):

    python servidor_teste/servidor.py
    python ferramentas/simulador.py

Botão (como na placa):
    .            toque: desce o cursor
    -            segurar: escolhe o item do cursor
    !            segurar 5 s: modo operador (carrinho vazio)
    ..-          vários de uma vez (2 toques e segurar)

Atalhos (mais rápidos para testar):
    1 a 9        inclui o produto           0    próxima página
    *            desfaz a última inclusão   #    vai para o pagamento
    112#         várias teclas de uma vez

Outros comandos:
    c A1B2C3D4   aproxima o cartão com esse UID
    (Enter)      sai da tela de resultado
    off / on     simula queda / volta do Wi-Fi
    sync         envia uma venda da fila offline
    fila         mostra as vendas pendentes
    sair         encerra

Modo operador: com o carrinho vazio, "!" (ou ***) e depois o PIN. Atalho:
***2580#. O PIN do simulador é 2580, ou o da variável BYTEBANK_PIN.

Comandos do gerente: o simulador busca um comando no servidor depois de
cada linha digitada (na placa, a cada 10 s). Para criar um, em outro
terminal: python ferramentas/enviar_comando.py --help
"""

import os
import sys
import time

AQUI = os.path.dirname(os.path.abspath(__file__))
RAIZ = os.path.dirname(AQUI)
sys.path.insert(0, os.path.join(AQUI, "compat"))       # urequests do computador
sys.path.insert(1, os.path.join(RAIZ, "esp32"))        # código da placa

# Funções de tempo que só existem no MicroPython
time.ticks_ms = lambda: int(time.monotonic() * 1000)
time.ticks_diff = lambda a, b: a - b
time.sleep_ms = lambda ms: time.sleep(ms / 1000)

from api import ClienteAPI               # noqa: E402  (precisa vir depois do sys.path)
from carrinho import Carrinho            # noqa: E402
from catalogo import Catalogo            # noqa: E402
from comandos import Comandos            # noqa: E402
from fila_offline import FilaContingencia  # noqa: E402
from pagamentos import Pagamentos        # noqa: E402
from terminal import SEGURAR, SEGURAR_LONGO, TOQUE, Terminal            # noqa: E402

API_URL = os.environ.get("BYTEBANK_API_URL", "http://127.0.0.1:8000")
PASTA_DE_DADOS = os.path.join(AQUI, "dados_simulador")   # faz o papel da flash
PIN = os.environ.get("BYTEBANK_PIN", "2580")
TERMINAL_ID = os.environ.get("BYTEBANK_TERMINAL", "POS-01")   # o mesmo que o enviar_comando.py usa


BOTAO = {".": TOQUE, "-": SEGURAR, "!": SEGURAR_LONGO}


class DisplayConsole:
    def mostrar(self, linhas):
        print("+----------------+")
        for texto in list(linhas) + [""] * (8 - len(linhas)):
            print("|{:<16}|".format(texto))
        print("+----------------+")


class SinalConsole:
    def tecla(self): pass
    def erro(self): print("   (bip bip - erro)")
    def aprovado(self): print("   (biiip - LED verde aceso)")
    def recusado(self): print("   (bip bip bip - recusado)")
    def apagar(self): pass


def main():
    os.makedirs(PASTA_DE_DADOS, exist_ok=True)
    os.chdir(PASTA_DE_DADOS)
    wifi = {"ligado": True}
    conectado = lambda: wifi["ligado"]

    api = ClienteAPI(API_URL, TERMINAL_ID)
    catalogo = Catalogo()
    catalogo.iniciar(api)
    fila = FilaContingencia()
    pagamentos = Pagamentos(api, fila, TERMINAL_ID, conectado)
    terminal = Terminal(DisplayConsole(), SinalConsole(), catalogo, Carrinho(), pagamentos,
                        conectado=conectado, relogio_ms=time.ticks_ms, diferenca_ms=time.ticks_diff,
                        pin_operador=PIN)
    comandos = Comandos(api, terminal, catalogo)
    comandos.restaurar_bloqueio()
    print("Simulador pronto. Servidor: {} | Comandos no início do arquivo.".format(API_URL))

    while True:
        try:
            comando = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if comando == "sair":
            break
        elif comando == "":
            if terminal.estado == "resultado":
                terminal.tecla("*")     # na placa, qualquer tecla volta ao catálogo
        elif comando.startswith("c "):
            terminal.cartao(comando[2:].strip().upper())
        elif comando in ("off", "on"):
            wifi["ligado"] = comando == "on"
            print("Wi-Fi {}.".format("ligado" if wifi["ligado"] else "DESLIGADO"))
            terminal.redesenhar()
        elif comando == "sync":
            print("Fila andou." if pagamentos.sincronizar() else "Nada enviado.")
        elif comando == "fila":
            for transacao in fila.pendentes():
                print("  {}  {} centavos  {}".format(transacao["id_transacao"],
                                                     transacao["total_centavos"], transacao["criada_em"]))
            print("  {} pendente(s)".format(len(fila)))
        else:
            for tecla in comando:
                if tecla in BOTAO:
                    terminal.tecla(BOTAO[tecla])
                elif tecla in "0123456789*#":
                    terminal.tecla(tecla)
        terminal.tick()
        if conectado() and terminal.livre():
            comandos.verificar()


if __name__ == "__main__":
    main()
