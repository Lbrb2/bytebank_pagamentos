"""
operador.py - Modo operador do terminal (MicroPython)

Um menu escondido, protegido por PIN, para quem cuida do caixa:

    Cancelar venda   estorna a última venda
    Resumo caixa     vendas e cancelamentos deste terminal
    Enviar fila      envia agora as vendas da fila offline

Como abrir: com o carrinho VAZIO, segure o botão por 5 segundos.

Como digitar o PIN com um botão só: o dígito da vez aparece na tela.
Cada TOQUE soma 1 (0, 1, 2 ... 9, 0...) e SEGURAR confirma o dígito.
Depois do último dígito, o PIN é conferido sozinho.

No menu, TOQUE desce para a próxima opção e SEGURAR escolhe. Segurar
5 s sai do modo operador a qualquer momento.

(No simulador também dá para usar as teclas do teclado numérico: dígitos,
# confirma e * apaga/sai. São atalhos para testar mais rápido.)

O PIN fica no config.py (PIN_OPERADOR), que não vai para o GitHub. Se o
config.py não tiver PIN, o modo operador fica desligado.

Segurança:
  * os dígitos já confirmados aparecem como * na tela;
  * depois de 3 PINs errados, o modo fica travado por 60 segundos;
  * o terminal volta sozinho ao catálogo após 30 s sem uso
    (controlado pelo terminal.py).

Esta classe só cuida das telas e dos comandos do menu. Quem fala com o
servidor é o pagamentos.py.
"""

from formatacao import centralizar, esquerda_direita, quebrar, reais

TOQUE = "toque"
SEGURAR = "segurar"
SEGURAR_LONGO = "segurar_longo"

TENTATIVAS_DE_PIN = 3
TRAVA_APOS_ERROS_MS = 60000
TAMANHO_MAXIMO_DO_PIN = 8

_PIN = "pin"
_MENU = "menu"
_CONFIRMAR = "confirmar"
_INFO = "info"

# (texto na tela, tecla de atalho)
OPCOES = (("Cancelar venda", "1"), ("Resumo caixa", "2"), ("Enviar fila", "3"), ("Sair", "*"))


def pin_valido(pin):
    """O PIN precisa ter de 4 a 8 dígitos."""
    return isinstance(pin, str) and 4 <= len(pin) <= TAMANHO_MAXIMO_DO_PIN and pin.isdigit()


class ModoOperador:
    def __init__(self, pin, pagamentos, catalogo, display, relogio_ms, diferenca_ms):
        self._pin = pin
        self._pagamentos = pagamentos
        self._catalogo = catalogo
        self._display = display
        self._agora = relogio_ms
        self._diferenca = diferenca_ms

        self._etapa = _PIN
        self._digitado = ""
        self._digito = 0             # dígito da vez (entrada pelo botão)
        self._aviso = ""
        self._erros = 0
        self._travado_desde = None
        self._opcao = 0               # opção do menu sob o cursor
        self._info = []
        self._sair_depois_da_info = False

    # ---------------- Chamado pelo terminal ----------------

    def abrir(self):
        self._digitado = ""
        self._digito = 0
        self._aviso = ""
        self._opcao = 0
        restante = self._tempo_travado()
        if restante:
            self._mostrar_info(["MODO OPERADOR", "", "Muitos PINs", "errados.", "",
                                "Tente em {} s".format(restante)], sair=True)
        else:
            self._etapa = _PIN

    def fechar(self):
        """Esquece o que foi digitado (o PIN não fica na memória à toa)."""
        self._digitado = ""
        self._digito = 0
        self._etapa = _PIN

    def tecla(self, evento):
        """Trata um evento do botão (ou uma tecla do simulador).
        Devolve True quando o operador sai do modo."""
        if evento == SEGURAR_LONGO:
            self.fechar()
            return True
        if self._etapa == _PIN:
            return self._evento_pin(evento)
        if self._etapa == _MENU:
            return self._evento_menu(evento)
        if self._etapa == _CONFIRMAR:
            if evento in (SEGURAR, "#"):
                self._cancelar_ultima()
            else:
                self._etapa = _MENU
            return False
        # _INFO: qualquer evento volta ao menu (ou sai, se era o aviso de trava)
        if self._sair_depois_da_info:
            self.fechar()
            return True
        self._etapa = _MENU
        return False

    def linhas(self):
        if self._etapa == _PIN:
            return [centralizar("MODO OPERADOR"), "", "Digite o PIN:",
                    centralizar("*" * len(self._digitado) + str(self._digito)), "",
                    self._aviso, "Toque: muda", "Segure: confirma"]
        if self._etapa == _MENU:
            linhas = [centralizar("MODO OPERADOR"), ""]
            for numero, (texto, _) in enumerate(OPCOES):
                linhas.append((">" if numero == self._opcao else " ") + texto)
            return linhas + ["", "Segure: escolhe"]
        if self._etapa == _CONFIRMAR:
            venda = self._pagamentos.ultima_venda
            unidades = sum(item["quantidade"] for item in venda["itens"])
            return [centralizar("CANCELAR VENDA?"), "",
                    esquerda_direita("Total", reais(venda["total_centavos"])),
                    esquerda_direita("Itens", str(unidades)), "", "",
                    "Segure: sim", "Toque: nao"]
        return self._info[:7] + [""] * (7 - len(self._info[:7])) + ["Aperte p/ voltar"]

    # ---------------- PIN ----------------

    def _tempo_travado(self):
        """Segundos que faltam para destravar, ou 0."""
        if self._travado_desde is None:
            return 0
        passado = self._diferenca(self._agora(), self._travado_desde)
        if passado >= TRAVA_APOS_ERROS_MS:
            self._travado_desde = None
            self._erros = 0
            return 0
        return (TRAVA_APOS_ERROS_MS - passado + 999) // 1000

    def _evento_pin(self, evento):
        if evento == TOQUE:
            self._digito = (self._digito + 1) % 10
        elif evento == SEGURAR:
            self._digitado += str(self._digito)
            self._digito = 0
            self._aviso = ""
            if len(self._digitado) >= len(self._pin):
                self._conferir_pin()
        elif evento == "*":                      # atalho do simulador
            if not self._digitado:
                self.fechar()
                return True
            self._digitado = self._digitado[:-1]
        elif evento == "#":
            self._conferir_pin()
        elif evento.isdigit() and len(self._digitado) < TAMANHO_MAXIMO_DO_PIN:
            self._digitado += evento
            self._aviso = ""
        return False

    def _conferir_pin(self):
        if self._digitado == self._pin:
            self._erros = 0
            self._digitado = ""
            self._opcao = 0
            self._etapa = _MENU
            print("[OPERADOR] Acesso liberado.")
            return
        self._erros += 1
        self._digitado = ""
        self._digito = 0
        print("[OPERADOR] PIN incorreto ({}/{}).".format(self._erros, TENTATIVAS_DE_PIN))
        if self._erros >= TENTATIVAS_DE_PIN:
            self._travado_desde = self._agora()
            self._mostrar_info(["MODO OPERADOR", "", "PIN incorreto", "3 vezes.", "",
                                "Travado por", "{} s".format(TRAVA_APOS_ERROS_MS // 1000)],
                               sair=True)
        else:
            self._aviso = "PIN incorreto"

    # ---------------- Menu ----------------

    def _evento_menu(self, evento):
        if evento == TOQUE:
            self._opcao = (self._opcao + 1) % len(OPCOES)
            return False
        if evento == SEGURAR:
            evento = OPCOES[self._opcao][1]
        if evento == "*":
            self.fechar()
            return True
        if evento == "1":
            if self._pagamentos.ultima_venda is None:
                self._mostrar_info(["CANCELAR VENDA", ""] + quebrar("Nenhuma venda para cancelar"))
            else:
                self._etapa = _CONFIRMAR
        elif evento == "2":
            self._resumo()
        elif evento == "3":
            self._enviar_fila()
        return False

    def _aguarde(self):
        self._display.mostrar(["", "", centralizar("Aguarde...")])

    def _cancelar_ultima(self):
        venda = self._pagamentos.ultima_venda
        self._aguarde()
        ok, mensagem = self._pagamentos.estornar_ultima()
        if ok:
            self._catalogo.devolver_estoque(venda["itens"])
            self._mostrar_info(["VENDA CANCELADA", "",
                                esquerda_direita("Valor", reais(venda["total_centavos"])), "",
                                "Dinheiro e", "estoque", "devolvidos."])
        else:
            self._mostrar_info(["NAO CANCELADA", ""] + quebrar(mensagem)[:4])

    def _resumo(self):
        self._aguarde()
        resumo, erro = self._pagamentos.resumo_do_caixa()
        fila = esquerda_direita("Fila offline", str(self._pagamentos.pendentes()))
        if resumo is None:
            self._mostrar_info(["RESUMO DO CAIXA", ""] + quebrar(erro) + ["", fila])
            return
        self._mostrar_info([
            "RESUMO DO CAIXA",
            esquerda_direita("Vendas", str(resumo.get("vendas", 0))),
            esquerda_direita("", reais(resumo.get("total_centavos", 0))),
            esquerda_direita("Canceladas", str(resumo.get("estornos", 0))),
            esquerda_direita("", reais(-resumo.get("estornado_centavos", 0))),
            fila,
        ])

    def _enviar_fila(self):
        self._aguarde()
        antes = self._pagamentos.pendentes()
        enviadas = self._pagamentos.enviar_fila()
        depois = self._pagamentos.pendentes()
        linhas = ["FILA OFFLINE", "", esquerda_direita("Enviadas", str(enviadas)),
                  esquerda_direita("Pendentes", str(depois))]
        if depois and antes == depois:
            linhas += ["", "Sem conexao com", "o servidor."]
        self._mostrar_info(linhas)

    def _mostrar_info(self, linhas, sair=False):
        self._info = [centralizar(linhas[0])] + list(linhas[1:])
        self._etapa = _INFO
        self._sair_depois_da_info = sair
