"""
terminal.py - Máquina de estados do ByteBank Pay (MicroPython)

O terminal é operado com UM botão:

    TOQUE          (rápido)          desce o cursor para o próximo item
    SEGURAR        (~1 s e soltar)   escolhe o item sob o cursor
    SEGURAR_LONGO  (5 s)             carrinho vazio: modo operador
                                     carrinho com itens: esvazia o carrinho

O terminal está sempre em um destes estados:

    CATALOGO ──"PAGAR"──> PAGAMENTO ──cartão──> RESULTADO ──(4 s ou botão)──> CATALOGO
      │  ^                  │
      │  └──── segurar ─────┘   (ou 30 s sem cartão)
      │
      ├── segurar 5 s (carrinho vazio) + PIN ──> OPERADOR ──(30 s sem uso)──> CATALOGO
      ├── comando "mensagem" ──> MENSAGEM ──(tempo ou botão)──> CATALOGO
      └── comando "bloquear" ──> BLOQUEADO ──comando "desbloquear"──> CATALOGO

  CATALOGO   lista os produtos com um cursor (>). Com itens no carrinho,
             a lista ganha no fim "PAGAR" e "Desfazer ultimo"
  PAGAMENTO  mostra o total e espera o cartão. Segurar volta ao catálogo
  RESULTADO  mostra aprovada, recusada ou registrada offline
  OPERADOR   menu protegido por PIN (ver operador.py)
  MENSAGEM   aviso enviado pelo gerente (comando remoto)
  BLOQUEADO  terminal travado pelo gerente: não vende até ser desbloqueado

Esta classe não sabe que hardware existe: recebe os eventos do botão e o
cartão já lidos, e devolve as linhas de texto para o display. Por isso pode
ser testada no computador, sem a placa.

Atalhos do simulador: além dos eventos do botão, tecla() aceita as teclas
de um teclado numérico (1-9 inclui o produto, # paga, * desfaz, 0 passa a
página, * * * abre o modo operador). Servem para testar mais rápido.
"""

from formatacao import ALTURA, centralizar, esquerda_direita, linha, quebrar, reais, valor
import pagamentos as pg

CATALOGO = "catalogo"
PAGAMENTO = "pagamento"
RESULTADO = "resultado"
OPERADOR = "operador"
MENSAGEM = "mensagem"
BLOQUEADO = "bloqueado"

# Eventos do botão (os mesmos textos do hardware.py)
TOQUE = "toque"
SEGURAR = "segurar"
SEGURAR_LONGO = "segurar_longo"

# Itens de ação que aparecem na lista do catálogo quando há algo no carrinho
PAGAR = "pagar"
DESFAZER = "desfazer"

PRODUTOS_POR_PAGINA = 5
TEMPO_RESULTADO_MS = 4000
TEMPO_PAGAMENTO_MS = 30000
TEMPO_OPERADOR_MS = 30000      # sai do modo operador após 30 s sem tecla
TEMPO_AVISO_MS = 1500
INTERVALO_REDESENHO_MS = 1000
ASTERISCOS_PARA_OPERADOR = 3
JANELA_ASTERISCOS_MS = 3000


class Terminal:
    def __init__(self, display, sinal, catalogo, carrinho, pagamentos,
                 conectado, relogio_ms, diferenca_ms, pin_operador=None):
        self._display = display
        self._sinal = sinal
        self._catalogo = catalogo
        self._carrinho = carrinho
        self._pagamentos = pagamentos
        self._conectado = conectado
        self._agora = relogio_ms
        self._diferenca = diferenca_ms

        self._operador = None
        if pin_operador is not None:
            from operador import ModoOperador, pin_valido
            if pin_valido(pin_operador):
                self._operador = ModoOperador(pin_operador, pagamentos, catalogo, display,
                                              relogio_ms, diferenca_ms)
            else:
                print("[TERMINAL] PIN_OPERADOR inválido (use de 4 a 8 dígitos). "
                      "Modo operador desligado.")

        self.estado = CATALOGO
        self._cursor = None          # item sob o cursor: código do produto, PAGAR ou DESFAZER
        self._resultado = None
        self._aviso = None
        self._aviso_desde = 0
        self._mensagem = ""
        self._duracao_mensagem = 0
        self._motivo_bloqueio = ""
        self._asteriscos = 0
        self._asterisco_desde = 0
        self._estado_desde = self._agora()
        self._ultima_tecla = self._agora()
        self._ultimo_redesenho = self._agora()
        self.redesenhar()

    # ---------------- Eventos ----------------

    def tecla(self, tecla):
        self._ultima_tecla = self._agora()
        if self.estado == CATALOGO:
            self._tecla_no_catalogo(tecla)
        elif self.estado == PAGAMENTO:
            if tecla in ("*", SEGURAR):
                self._mudar_para(CATALOGO)
        elif self.estado == RESULTADO:
            self._voltar_ao_catalogo()
        elif self.estado == OPERADOR:
            if self._operador.tecla(tecla):
                self._mudar_para(CATALOGO)
        elif self.estado == MENSAGEM:
            self._mudar_para(CATALOGO)
        elif self.estado == BLOQUEADO:
            self._sinal.erro()
        self.redesenhar()

    def cartao(self, uid):
        """Chamado quando o leitor RFID detecta um cartão."""
        if self.estado != PAGAMENTO:
            return
        total = self._carrinho.total_centavos(self._catalogo)
        self._display.mostrar(["", "", centralizar("Processando..."), "",
                               centralizar(reais(total))])
        itens = self._carrinho.itens()
        resultado = self._pagamentos.pagar(uid, itens, total)
        self._resultado = resultado
        if resultado["situacao"] in (pg.APROVADA, pg.OFFLINE):
            self._catalogo.baixar_estoque(itens)
            self._carrinho.limpar()
            self._cursor = None
            self._sinal.aprovado()
        else:
            # O carrinho é mantido: o cliente pode tentar outro cartão
            self._sinal.recusado()
        self._mudar_para(RESULTADO)
        self.redesenhar()

    def tick(self):
        """Chamado a cada volta do laço principal: trata os tempos limite."""
        agora = self._agora()
        tempo_no_estado = self._diferenca(agora, self._estado_desde)
        if self.estado == RESULTADO and tempo_no_estado >= TEMPO_RESULTADO_MS:
            self._voltar_ao_catalogo()
        elif self.estado == PAGAMENTO and tempo_no_estado >= TEMPO_PAGAMENTO_MS:
            self._mudar_para(CATALOGO)
            self._avisar("Tempo esgotado")
        elif self.estado == MENSAGEM and tempo_no_estado >= self._duracao_mensagem:
            self._mudar_para(CATALOGO)
        elif (self.estado == OPERADOR
              and self._diferenca(agora, self._ultima_tecla) >= TEMPO_OPERADOR_MS):
            self._operador.fechar()
            self._mudar_para(CATALOGO)
        if self._aviso is not None and self._diferenca(agora, self._aviso_desde) >= TEMPO_AVISO_MS:
            self._aviso = None
        if self._diferenca(agora, self._ultimo_redesenho) >= INTERVALO_REDESENHO_MS:
            self.redesenhar()   # atualiza o indicador de offline e os tempos

    # ---------------- Comandos remotos (chamados pelo comandos.py) ----------------

    def bloquear(self, motivo):
        self._motivo_bloqueio = motivo
        self._carrinho.limpar()
        if self.estado == OPERADOR:
            self._operador.fechar()
        self._mudar_para(BLOQUEADO)
        self.redesenhar()

    def desbloquear(self):
        if self.estado == BLOQUEADO:
            self._mudar_para(CATALOGO)
            self.redesenhar()

    def mostrar_mensagem(self, texto, duracao_ms):
        """Mostra um aviso do gerente. Só no catálogo: não interrompe uma
        venda nem aparece por cima do bloqueio. Devolve True se mostrou."""
        if self.estado != CATALOGO:
            return False
        self._mensagem = texto
        self._duracao_mensagem = duracao_ms
        self._mudar_para(MENSAGEM)
        self._sinal.tecla()
        self.redesenhar()
        return True

    def catalogo_atualizado(self):
        self._cursor = None
        self.redesenhar()

    # ---------------- Consultas para o main.py ----------------

    def aguardando_cartao(self):
        return self.estado == PAGAMENTO

    def bloqueado(self):
        return self.estado == BLOQUEADO

    def livre(self):
        """True se dá para executar um comando remoto agora: ninguém está no
        meio de uma compra nem no modo operador."""
        return self.estado in (CATALOGO, BLOQUEADO) and self._carrinho.vazio()

    def ocioso(self, desde_ms):
        """True se ninguém está usando o terminal: catálogo (ou bloqueado),
        carrinho vazio e nenhuma tecla há `desde_ms` milissegundos. É nessa
        hora que o main.py faz tarefas demoradas, como reconectar o Wi-Fi."""
        return (self.livre()
                and self._diferenca(self._agora(), self._ultima_tecla) >= desde_ms)

    # ---------------- Regras do catálogo ----------------

    def _itens_da_lista(self):
        """Itens que o cursor percorre: os produtos e, se houver algo no
        carrinho, as ações PAGAR e DESFAZER no fim."""
        itens = [produto["codigo"] for produto in self._catalogo.produtos()]
        if not self._carrinho.vazio():
            itens += [PAGAR, DESFAZER]
        return itens

    def _posicao_do_cursor(self):
        """(itens, índice do cursor). Se o item sumiu da lista (ex.: o
        carrinho esvaziou e o PAGAR saiu), o cursor volta ao primeiro."""
        itens = self._itens_da_lista()
        if self._cursor not in itens:
            self._cursor = itens[0] if itens else None
            return itens, 0
        return itens, itens.index(self._cursor)

    def _tecla_no_catalogo(self, tecla):
        if tecla != "*":
            self._asteriscos = 0

        if tecla == TOQUE:
            itens, indice = self._posicao_do_cursor()
            if itens:
                self._cursor = itens[(indice + 1) % len(itens)]
            self._sinal.tecla()
        elif tecla == SEGURAR:
            self._posicao_do_cursor()
            if self._cursor == PAGAR:
                self._ir_para_pagamento()
            elif self._cursor == DESFAZER:
                self._desfazer()
            elif self._cursor is not None:
                self._incluir(self._cursor)
        elif tecla == SEGURAR_LONGO:
            if self._carrinho.vazio():
                self._abrir_operador()
            else:
                self._carrinho.limpar()
                self._cursor = None
                self._sinal.erro()
                self._avisar("Carrinho limpo")

        # Atalhos do simulador (teclado numérico)
        elif tecla == "#":
            self._ir_para_pagamento()
        elif tecla == "*":
            if self._carrinho.vazio():
                self._asterisco_no_carrinho_vazio()
            else:
                self._desfazer()
        elif tecla == "0":
            itens, indice = self._posicao_do_cursor()
            proxima = (indice // PRODUTOS_POR_PAGINA + 1) * PRODUTOS_POR_PAGINA
            self._cursor = itens[proxima] if proxima < len(itens) else itens[0]
            self._sinal.tecla()
        else:
            self._cursor = tecla if self._catalogo.buscar(tecla) else self._cursor
            self._incluir(tecla)

    def _ir_para_pagamento(self):
        if self._carrinho.vazio():
            self._sinal.erro()
            self._avisar("Carrinho vazio")
        else:
            self._sinal.tecla()
            self._mudar_para(PAGAMENTO)

    def _desfazer(self):
        """Tira a última unidade incluída (topo da pilha do carrinho)."""
        codigo = self._carrinho.remover_ultimo()
        if codigo is None:
            self._sinal.erro()
            return
        self._sinal.tecla()
        if self._carrinho.vazio():
            self._cursor = codigo        # PAGAR e DESFAZER somem; fica no produto
        produto = self._catalogo.buscar(codigo)
        self._avisar("{}x {}".format(self._carrinho.quantidade(codigo),
                                      produto["nome"] if produto else codigo))

    def _incluir(self, tecla):
        produto = self._catalogo.buscar(tecla)
        if produto is None:
            self._sinal.erro()
            self._avisar("Nao ha produto {}".format(tecla))
            return
        estoque = self._catalogo.estoque(tecla)
        if estoque is not None and self._carrinho.quantidade(tecla) >= estoque:
            self._sinal.erro()
            self._avisar("Esgotado" if estoque == 0 else "So {} em estoque".format(estoque))
        elif not self._carrinho.adicionar(tecla):
            self._sinal.erro()
            self._avisar("Carrinho cheio")
        else:
            self._sinal.tecla()
            # Quantidade primeiro: se o nome for longo, só o fim dele é cortado
            self._avisar("{}x {}".format(self._carrinho.quantidade(tecla), produto["nome"]))

    def _abrir_operador(self):
        if self._operador is None:
            self._sinal.erro()
            return
        self._asteriscos = 0
        self._aviso = None
        self._sinal.tecla()
        self._operador.abrir()
        self._mudar_para(OPERADOR)

    def _asterisco_no_carrinho_vazio(self):
        """Três * seguidos com o carrinho vazio abrem o modo operador."""
        agora = self._agora()
        if (self._asteriscos == 0
                or self._diferenca(agora, self._asterisco_desde) > JANELA_ASTERISCOS_MS):
            self._asteriscos = 1
            self._asterisco_desde = agora
        else:
            self._asteriscos += 1
        if self._asteriscos >= ASTERISCOS_PARA_OPERADOR and self._operador is not None:
            self._abrir_operador()
        else:
            self._sinal.erro()

    # ---------------- Transições ----------------

    def _mudar_para(self, estado):
        self.estado = estado
        self._estado_desde = self._agora()

    def _voltar_ao_catalogo(self):
        self._sinal.apagar()
        self._resultado = None
        self._mudar_para(CATALOGO)

    def _avisar(self, texto):
        self._aviso = texto
        self._aviso_desde = self._agora()

    # ---------------- Telas ----------------

    def redesenhar(self):
        self._ultimo_redesenho = self._agora()
        if self.estado == CATALOGO:
            linhas = self._tela_catalogo()
        elif self.estado == PAGAMENTO:
            linhas = self._tela_pagamento()
        elif self.estado == OPERADOR:
            linhas = self._operador.linhas()
        elif self.estado == MENSAGEM:
            linhas = [centralizar("AVISO DO GERENTE"), ""] + quebrar(self._mensagem)[:6]
        elif self.estado == BLOQUEADO:
            linhas = ([centralizar("TERMINAL"), centralizar("BLOQUEADO"), ""]
                      + quebrar(self._motivo_bloqueio)[:5])
        else:
            linhas = self._tela_resultado()
        self._display.mostrar([linha(texto) for texto in linhas[:ALTURA]])

    def _offline(self):
        return not self._conectado()

    def _tela_catalogo(self):
        itens, indice = self._posicao_do_cursor()
        paginas = max(1, (len(itens) + PRODUTOS_POR_PAGINA - 1) // PRODUTOS_POR_PAGINA)
        pagina = indice // PRODUTOS_POR_PAGINA
        indicador = "{}/{}".format(pagina + 1, paginas) if paginas > 1 else ""
        if self._offline():
            indicador = (indicador + " OFF").strip()
        linhas = [esquerda_direita("PRODUTOS", indicador)]

        total = self._carrinho.total_centavos(self._catalogo)
        inicio = pagina * PRODUTOS_POR_PAGINA
        for item in itens[inicio:inicio + PRODUTOS_POR_PAGINA]:
            marca = ">" if item == self._cursor else " "
            if item == PAGAR:
                linhas.append(esquerda_direita(marca + "PAGAR", reais(total)))
            elif item == DESFAZER:
                linhas.append(marca + "Desfazer ultimo")
            else:
                produto = self._catalogo.buscar(item)
                preco = "esgot" if produto.get("estoque") == 0 else valor(produto["preco_centavos"])
                linhas.append(esquerda_direita(marca + produto["nome"], preco))
        while len(linhas) < 6:
            linhas.append("")

        if self._carrinho.vazio():
            linhas.append("Carrinho vazio")
        else:
            unidades = self._carrinho.unidades()
            linhas.append(esquerda_direita(
                "{} {}".format(unidades, "item" if unidades == 1 else "itens"), reais(total)))

        if self._aviso is not None:
            linhas.append(self._aviso)
        elif self._cursor == PAGAR:
            linhas.append("Segure p/ pagar")
        elif self._cursor == DESFAZER:
            linhas.append("Segure: desfazer")
        else:
            linhas.append("Toque:> Segure:+")
        return linhas

    def _tela_pagamento(self):
        total = self._carrinho.total_centavos(self._catalogo)
        return [
            centralizar("PAGAMENTO"),
            "",
            esquerda_direita("Total", reais(total)),
            esquerda_direita("Itens", str(self._carrinho.unidades())),
            "",
            centralizar("Aproxime o"),
            centralizar("cartao..."),
            esquerda_direita("Segure: volta", "OFF" if self._offline() else ""),
        ]

    def _tela_resultado(self):
        resultado = self._resultado or {"situacao": pg.RECUSADA, "motivo": "Erro"}
        situacao = resultado["situacao"]
        if situacao == pg.APROVADA:
            linhas = [centralizar("APROVADA"), "",
                      esquerda_direita("Total", reais(resultado["total_centavos"]))]
            if resultado.get("saldo_centavos") is not None:
                linhas.append(esquerda_direita("Saldo", reais(resultado["saldo_centavos"])))
            linhas += ["", centralizar("Obrigado!")]
            return linhas
        if situacao == pg.OFFLINE:
            return [centralizar("REGISTRADA"), centralizar("(sem conexao)"), "",
                    esquerda_direita("Total", reais(resultado["total_centavos"])), "",
                    "Sera confirmada", "quando a rede", "voltar."]
        motivo = quebrar(resultado.get("motivo") or "Recusada")
        return [centralizar("RECUSADA"), ""] + motivo[:4] + ["", "Aperte p/ voltar"]
