"""
servicos.py - Regras de negócio do ByteBank Pay

Duas camadas trabalham juntas:

  TABELAS HASH (memória)  respondem "quem é?" em O(1):
      cartoes      UID do cartão     -> cartão, conta e titular
      produtos     código da tecla   -> produto (id, nome e preço)
      terminais    código (POS-01)   -> id do terminal no banco
      processadas  id_transacao      -> resposta já dada (idempotência)
    São carregadas do banco quando o servidor liga e atualizadas depois de
    cada COMMIT. Assim, o terminal nunca espera uma consulta ao banco só
    para descobrir de quem é o cartão.

  BANCO (SQLite)  guarda o que MUDA e precisa ser exato: saldo e estoque.
    Esses valores são lidos e alterados dentro da transação, com UPDATE
    condicional ("... WHERE saldo_centavos >= total"). Se a condição falhar,
    a venda inteira é desfeita (ROLLBACK).

Por isso, quem altera cartões ou preços deve passar pela API (ou reiniciar
o servidor depois de mexer direto no banco), para as tabelas hash não
ficarem desatualizadas.

Todas as operações rodam com uma trava (threading.Lock): o FastAPI atende
várias requisições ao mesmo tempo, e duas vendas do mesmo cartão não podem
se misturar.
"""

import heapq
import json
import logging
import threading
from datetime import datetime, timezone

from .antifraude import Antifraude
from .banco import transacao
from .tabela_hash import TabelaHash

log = logging.getLogger("bytebank")


def agora():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def reais(centavos):
    texto = "{:,.2f}".format(centavos / 100)
    return "R$ " + texto.replace(",", "X").replace(".", ",").replace("X", ".")


class Recusa(Exception):
    """Regra de negócio que impede a operação (vira HTTP 4xx com um motivo)."""

    def __init__(self, motivo, codigo_http=422):
        super().__init__(motivo)
        self.motivo = motivo
        self.codigo_http = codigo_http


class Servico:
    def __init__(self, conexao, antifraude=None):
        self._con = conexao
        self._trava = threading.Lock()
        self.antifraude = antifraude or Antifraude()
        self.cartoes = TabelaHash()
        self.produtos = TabelaHash()
        self.terminais = TabelaHash()
        self.processadas = TabelaHash()
        self._carregar_tabelas_hash()
        self.antifraude.carregar_historico(conexao)

    # ==================================================================
    # Tabelas hash
    # ==================================================================

    def _carregar_tabelas_hash(self):
        for linha in self._con.execute(
                "SELECT c.id, c.uid_rfid, c.conta_id, c.status, c.motivo_bloqueio, cl.nome AS titular "
                "FROM cartoes c JOIN contas ct ON ct.id = c.conta_id "
                "JOIN clientes cl ON cl.id = ct.cliente_id"):
            self.cartoes.inserir(linha["uid_rfid"], dict(linha))
        for linha in self._con.execute("SELECT id, codigo, nome, preco_centavos FROM produtos"):
            self.produtos.inserir(linha["codigo"], dict(linha))
        for linha in self._con.execute("SELECT id, codigo FROM terminais"):
            self.terminais.inserir(linha["codigo"], linha["id"])
        for linha in self._con.execute("SELECT id_transacao, codigo_http, resposta FROM transacoes"):
            self.processadas.inserir(linha["id_transacao"],
                                     (linha["codigo_http"], json.loads(linha["resposta"])))
        log.info("Tabelas hash carregadas: %d cartões, %d produtos, %d terminais, %d vendas.",
                 len(self.cartoes), len(self.produtos), len(self.terminais), len(self.processadas))

    def estatisticas_tabelas_hash(self):
        return {nome: tabela.estatisticas() for nome, tabela in (
            ("cartoes", self.cartoes), ("produtos", self.produtos),
            ("terminais", self.terminais), ("processadas", self.processadas))}

    def _id_terminal(self, codigo):
        """Id do terminal pelo código, em O(1). Um terminal novo é cadastrado
        no primeiro contato (e isso fica na auditoria)."""
        terminal_id = self.terminais.buscar(codigo)
        if terminal_id is None:
            with transacao(self._con) as con:
                con.execute("INSERT OR IGNORE INTO terminais (codigo) VALUES (?)", (codigo,))
                terminal_id = con.execute("SELECT id FROM terminais WHERE codigo = ?",
                                          (codigo,)).fetchone()["id"]
                self._auditar(con, "terminal_cadastrado", terminal_id=terminal_id, detalhes=codigo)
            self.terminais.inserir(codigo, terminal_id)
            log.info("Terminal novo cadastrado: %s", codigo)
        return terminal_id

    def _registrar_contato(self, terminal_id):
        self._con.execute("UPDATE terminais SET ultimo_contato = ? WHERE id = ?", (agora(), terminal_id))

    @staticmethod
    def _auditar(con, evento, terminal_id=None, conta_id=None, transacao_id=None,
                 valor_anterior=None, valor_novo=None, detalhes=None):
        con.execute(
            "INSERT INTO logs_auditoria (evento, terminal_id, conta_id, transacao_id, "
            "valor_anterior, valor_novo, detalhes) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (evento, terminal_id, conta_id, transacao_id, valor_anterior, valor_novo, detalhes))

    # ==================================================================
    # Catálogo
    # ==================================================================

    def catalogo(self):
        with self._trava:
            linhas = self._con.execute(
                "SELECT codigo, nome, preco_centavos, estoque FROM produtos ORDER BY codigo").fetchall()
        return [dict(linha) for linha in linhas]

    def alterar_produto(self, codigo, preco_centavos=None, estoque=None, estoque_esperado=None):
        """Muda o preço e/ou corrige a contagem do estoque (valor absoluto).

        Com estoque_esperado, a correção é um "compare-and-set": o UPDATE só
        acontece se o estoque ainda for o que o gerente viu. Se uma venda
        mudou o valor nesse meio-tempo, nada é gravado (nem o preço) e a
        resposta é 409, em vez de a venda sumir da conta.
        """
        with self._trava:
            produto = self.produtos.buscar(codigo)
            if produto is None:
                raise Recusa("Produto {} não encontrado".format(codigo), 404)
            with transacao(self._con) as con:
                if preco_centavos is not None:
                    con.execute("UPDATE produtos SET preco_centavos = ? WHERE id = ?",
                                (preco_centavos, produto["id"]))
                if estoque is not None and estoque_esperado is None:
                    con.execute("UPDATE produtos SET estoque = ? WHERE id = ?", (estoque, produto["id"]))
                elif estoque is not None:
                    alteradas = con.execute("UPDATE produtos SET estoque = ? WHERE id = ? AND estoque = ?",
                                            (estoque, produto["id"], estoque_esperado)).rowcount
                    if alteradas == 0:          # levanta dentro do bloco: ROLLBACK de tudo
                        atual = con.execute("SELECT estoque FROM produtos WHERE id = ?",
                                            (produto["id"],)).fetchone()[0]
                        raise Recusa("O estoque mudou de {} para {} enquanto você editava (houve venda). "
                                     "Confira e salve de novo.".format(estoque_esperado, atual), 409)
                linha = dict(con.execute("SELECT id, codigo, nome, preco_centavos, estoque "
                                         "FROM produtos WHERE id = ?", (produto["id"],)).fetchone())
            # Só depois do COMMIT a tabela hash recebe o preço novo
            self.produtos.inserir(codigo, {k: linha[k] for k in ("id", "codigo", "nome", "preco_centavos")})
            log.info("Produto %s alterado: preço %s, estoque %d.", codigo,
                     reais(linha["preco_centavos"]), linha["estoque"])
            del linha["id"]
            return linha

    def repor_estoque(self, codigo, unidades):
        """Chegou mercadoria: SOMA `unidades` ao estoque, numa conta feita pelo
        próprio banco (estoque = estoque + ?). Vendas feitas enquanto o
        gerente digitava continuam descontadas: não há atualização perdida."""
        with self._trava:
            produto = self.produtos.buscar(codigo)
            if produto is None:
                raise Recusa("Produto {} não encontrado".format(codigo), 404)
            with transacao(self._con) as con:
                con.execute("UPDATE produtos SET estoque = estoque + ? WHERE id = ?", (unidades, produto["id"]))
                linha = dict(con.execute("SELECT codigo, nome, preco_centavos, estoque "
                                         "FROM produtos WHERE id = ?", (produto["id"],)).fetchone())
            log.info("Reposição: +%d %s, estoque agora %d.", unidades, linha["nome"], linha["estoque"])
            return linha

    # ==================================================================
    # Vendas
    # ==================================================================

    def processar_venda(self, venda):
        """Processa uma venda do terminal. Devolve (codigo_http, resposta).

        `venda` já chega validada (formato) pelo FastAPI. Aqui ficam as
        regras de negócio e a gravação atômica.
        """
        with self._trava:
            # 1. Idempotência: a mesma venda chegando de novo (O(1))
            anterior = self.processadas.buscar(venda["id_transacao"])
            if anterior is not None:
                codigo_http, resposta = anterior
                log.info("Venda %s REPETIDA: devolvendo a resposta original, sem cobrar de novo.",
                         venda["id_transacao"])
                return codigo_http, dict(resposta, repetida=True)

            terminal_id = self._id_terminal(venda["terminal"])
            self._registrar_contato(terminal_id)
            cartao = self.cartoes.buscar(venda["uid_cartao"])          # 2. cartão em O(1)
            try:
                codigo_http, resposta = self._cobrar(venda, terminal_id, cartao)
            except Recusa as recusa:
                codigo_http = recusa.codigo_http
                resposta = {"status": "recusada", "motivo": recusa.motivo}
                self._registrar_recusa(venda, terminal_id, cartao, codigo_http, resposta)
                log.info("Venda %s RECUSADA: %s", venda["id_transacao"], recusa.motivo)

            self.processadas.inserir(venda["id_transacao"], (codigo_http, resposta))
            return codigo_http, resposta

    def _cobrar(self, venda, terminal_id, cartao):
        if cartao is None:
            log.warning(">>> Cartão DESCONHECIDO: UID %s. Para aceitá-lo, cadastre-o na tabela cartoes.",
                        venda["uid_cartao"])
            raise Recusa("Cartão não cadastrado")
        if cartao["status"] != "ativo":
            raise Recusa("Cartão bloqueado por segurança"
                         if (cartao.get("motivo_bloqueio") or "").startswith("Suspeita")
                         else "Cartão bloqueado")

        # Terminal bloqueado pelo gerente não faz vendas novas. As vendas feitas
        # offline ANTES do bloqueio ainda são aceitas quando chegam pela fila.
        if venda["origem"] != "contingencia":
            status = self._con.execute("SELECT status FROM terminais WHERE id = ?",
                                       (terminal_id,)).fetchone()["status"]
            if status == "bloqueado":
                raise Recusa("Terminal bloqueado")

        # 3. Produtos em O(1) e total calculado com os preços do SERVIDOR. O
        #    total enviado pelo terminal é ignorado: um terminal adulterado
        #    não escolhe o preço.
        quantidades = {}
        for item in venda["itens"]:
            quantidades[item["codigo"]] = quantidades.get(item["codigo"], 0) + item["quantidade"]
        produtos = {}
        total = 0
        for codigo, quantidade in quantidades.items():
            produto = self.produtos.buscar(codigo)
            if produto is None:
                raise Recusa("Produto {} inexistente".format(codigo))
            produtos[codigo] = produto
            total += produto["preco_centavos"] * quantidade
        if venda.get("total_centavos") not in (None, total):
            log.warning("Venda %s: o terminal calculou %s, o servidor cobrou %s.",
                        venda["id_transacao"], reais(venda["total_centavos"]), reais(total))

        # Antifraude (extra): duas regras em O(1); se disparar, bloqueia o cartão já
        suspeita = self.antifraude.avaliar(venda["uid_cartao"], total, venda["origem"])
        if suspeita:
            self._bloquear_por_suspeita(cartao, terminal_id, venda, "Suspeita de fraude: " + suspeita)
            raise Recusa("Cartão bloqueado por segurança")

        # 4. Gravação ATÔMICA: estoque, saldo, venda, itens e auditoria juntos.
        #    Qualquer Recusa no meio desfaz tudo o que já foi feito (ROLLBACK).
        with transacao(self._con) as con:
            for codigo, quantidade in quantidades.items():
                produto = produtos[codigo]
                alterou = con.execute(
                    "UPDATE produtos SET estoque = estoque - ? WHERE id = ? AND estoque >= ?",
                    (quantidade, produto["id"], quantidade)).rowcount
                if not alterou:
                    raise Recusa("Sem estoque de {}".format(produto["nome"]))

            alterou = con.execute(
                "UPDATE contas SET saldo_centavos = saldo_centavos - ? "
                "WHERE id = ? AND saldo_centavos >= ?",
                (total, cartao["conta_id"], total)).rowcount
            if not alterou:
                raise Recusa("Saldo insuficiente")
            saldo = con.execute("SELECT saldo_centavos FROM contas WHERE id = ?",
                                (cartao["conta_id"],)).fetchone()["saldo_centavos"]

            transacao_id = self._inserir_transacao(con, venda, terminal_id, cartao["id"], total,
                                                   "aprovada", None, 200, {})
            for codigo, quantidade in quantidades.items():
                con.execute(
                    "INSERT INTO itens_transacao (transacao_id, produto_id, quantidade, "
                    "preco_unitario_centavos) VALUES (?, ?, ?, ?)",
                    (transacao_id, produtos[codigo]["id"], quantidade, produtos[codigo]["preco_centavos"]))
            resposta = {"status": "aprovada", "transacao_id": transacao_id, "total_centavos": total,
                        "saldo_centavos": saldo, "titular": cartao["titular"]}
            con.execute("UPDATE transacoes SET resposta = ? WHERE id = ?",
                        (json.dumps(resposta, ensure_ascii=False), transacao_id))
            self._auditar(con, "venda_aprovada", terminal_id=terminal_id, conta_id=cartao["conta_id"],
                          transacao_id=transacao_id, valor_novo=total, detalhes=venda["id_transacao"])

        if venda["origem"] == "online":
            self.antifraude.registrar_aprovada(venda["uid_cartao"], total)
        log.info("Venda %s APROVADA: %s (%s). Saldo: %s.", venda["id_transacao"], reais(total),
                 cartao["titular"], reais(saldo))
        return 200, resposta

    # ==================================================================
    # Antifraude: bloqueio preventivo e desbloqueio pelo gerente
    # ==================================================================

    def _bloquear_por_suspeita(self, cartao, terminal_id, venda, motivo):
        with transacao(self._con) as con:
            con.execute("UPDATE cartoes SET status = 'bloqueado', motivo_bloqueio = ?, bloqueado_em = ? "
                        "WHERE id = ?", (motivo, agora(), cartao["id"]))
            self._auditar(con, "fraude_suspeita", terminal_id=terminal_id, conta_id=cartao["conta_id"],
                          detalhes="{}: {} (venda {})".format(venda["uid_cartao"], motivo,
                                                             venda["id_transacao"]))
        self.cartoes.inserir(venda["uid_cartao"], dict(cartao, status="bloqueado", motivo_bloqueio=motivo))
        log.warning(">>> CARTÃO %s BLOQUEADO POR SEGURANÇA: %s", venda["uid_cartao"], motivo)

    def cartoes_bloqueados(self):
        with self._trava:
            linhas = self._con.execute(
                "SELECT c.uid_rfid, c.apelido, cl.nome AS titular, c.motivo_bloqueio, c.bloqueado_em "
                "FROM cartoes c JOIN contas ct ON ct.id = c.conta_id JOIN clientes cl ON cl.id = ct.cliente_id "
                "WHERE c.status = 'bloqueado' ORDER BY c.bloqueado_em DESC, c.id").fetchall()
        return [dict(linha) for linha in linhas]

    def desbloquear_cartao(self, uid):
        uid = uid.upper()
        with self._trava:
            cartao = self.cartoes.buscar(uid)
            if cartao is None:
                raise Recusa("Cartão não cadastrado", 404)
            with transacao(self._con) as con:
                con.execute("UPDATE cartoes SET status = 'ativo', motivo_bloqueio = NULL, bloqueado_em = NULL "
                            "WHERE id = ?", (cartao["id"],))
                self._auditar(con, "cartao_desbloqueado", conta_id=cartao["conta_id"],
                              detalhes="{} (antes: {})".format(uid, cartao.get("motivo_bloqueio") or "bloqueado"))
            self.cartoes.inserir(uid, dict(cartao, status="ativo", motivo_bloqueio=None))
            self.antifraude.esquecer_tentativas(uid)
        log.info("Cartão %s desbloqueado pelo gerente.", uid)
        return {"uid": uid, "status": "ativo"}

    def _registrar_recusa(self, venda, terminal_id, cartao, codigo_http, resposta):
        """A recusa também fica registrada (em outra transação, já que a da
        venda foi desfeita): aparece no histórico, no dashboard e na auditoria."""
        with transacao(self._con) as con:
            transacao_id = self._inserir_transacao(
                con, venda, terminal_id, cartao["id"] if cartao else None, 0,
                "recusada", resposta["motivo"], codigo_http, resposta)
            self._auditar(con, "venda_recusada", terminal_id=terminal_id,
                          conta_id=cartao["conta_id"] if cartao else None,
                          transacao_id=transacao_id, detalhes=resposta["motivo"])

    @staticmethod
    def _inserir_transacao(con, venda, terminal_id, cartao_id, total, status, motivo,
                           codigo_http, resposta):
        return con.execute(
            "INSERT INTO transacoes (id_transacao, terminal_id, cartao_id, uid_cartao, total_centavos, "
            "status, motivo, origem, criada_no_terminal, relogio_sincronizado, codigo_http, resposta) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (venda["id_transacao"], terminal_id, cartao_id, venda["uid_cartao"], total, status, motivo,
             venda["origem"], venda.get("criada_em"), int(bool(venda.get("relogio_sincronizado"))),
             codigo_http, json.dumps(resposta, ensure_ascii=False))).lastrowid

    # ==================================================================
    # Estorno e resumo do caixa (modo operador)
    # ==================================================================

    def estornar(self, id_transacao, terminal):
        """Cancela uma venda aprovada: devolve o dinheiro e o estoque, juntos.
        Idempotente: repetido, não devolve o dinheiro duas vezes."""
        with self._trava:
            venda = self._con.execute(
                "SELECT t.id, t.status, t.total_centavos, t.terminal_id, te.codigo AS terminal, "
                "c.conta_id FROM transacoes t JOIN terminais te ON te.id = t.terminal_id "
                "LEFT JOIN cartoes c ON c.id = t.cartao_id WHERE t.id_transacao = ?",
                (id_transacao,)).fetchone()
            if venda is None:
                raise Recusa("Venda não encontrada", 404)
            if venda["terminal"] != terminal:
                raise Recusa("Venda de outro terminal", 403)
            if venda["status"] == "recusada":
                raise Recusa("Venda recusada não pode ser cancelada", 409)
            self._registrar_contato(venda["terminal_id"])
            if venda["status"] == "estornada":
                saldo = self._saldo(venda["conta_id"])
                return 200, {"status": "estornada", "repetido": True,
                             "total_centavos": venda["total_centavos"], "saldo_centavos": saldo}

            with transacao(self._con) as con:
                itens = con.execute("SELECT produto_id, quantidade FROM itens_transacao "
                                    "WHERE transacao_id = ?", (venda["id"],)).fetchall()
                for item in itens:
                    con.execute("UPDATE produtos SET estoque = estoque + ? WHERE id = ?",
                                (item["quantidade"], item["produto_id"]))
                con.execute("UPDATE contas SET saldo_centavos = saldo_centavos + ? WHERE id = ?",
                            (venda["total_centavos"], venda["conta_id"]))
                con.execute("UPDATE transacoes SET status = 'estornada' WHERE id = ? AND status = 'aprovada'",
                            (venda["id"],))
                con.execute("INSERT INTO estornos (transacao_id, terminal_id, valor_centavos) "
                            "VALUES (?, ?, ?)", (venda["id"], venda["terminal_id"], venda["total_centavos"]))
                self._auditar(con, "estorno", terminal_id=venda["terminal_id"], conta_id=venda["conta_id"],
                              transacao_id=venda["id"], valor_novo=venda["total_centavos"],
                              detalhes=id_transacao)
                saldo = con.execute("SELECT saldo_centavos FROM contas WHERE id = ?",
                                    (venda["conta_id"],)).fetchone()["saldo_centavos"]
            log.info("Venda %s ESTORNADA: %s devolvidos.", id_transacao, reais(venda["total_centavos"]))
            return 200, {"status": "estornada", "total_centavos": venda["total_centavos"],
                         "saldo_centavos": saldo}

    def _saldo(self, conta_id):
        linha = self._con.execute("SELECT saldo_centavos FROM contas WHERE id = ?", (conta_id,)).fetchone()
        return linha["saldo_centavos"] if linha else None

    def resumo_do_caixa(self, terminal):
        with self._trava:
            terminal_id = self._id_terminal(terminal)
            self._registrar_contato(terminal_id)
            linha = self._con.execute("SELECT * FROM vw_resumo_caixa WHERE terminal = ?",
                                      (terminal,)).fetchone()
        resumo = dict(linha)
        del resumo["terminal"]
        return resumo

    # ==================================================================
    # Comandos remotos (fila FIFO por terminal, guardada no banco)
    # ==================================================================

    def criar_comando(self, terminal, tipo, parametros):
        with self._trava:
            terminal_id = self._id_terminal(terminal)
            with transacao(self._con) as con:
                comando_id = con.execute(
                    "INSERT INTO comandos (terminal_id, tipo, parametros) VALUES (?, ?, ?)",
                    (terminal_id, tipo, json.dumps(parametros, ensure_ascii=False))).lastrowid
                # O bloqueio vale no servidor na hora: mesmo antes de o terminal
                # buscar o comando, ele já não consegue aprovar vendas novas.
                if tipo == "bloquear":
                    con.execute("UPDATE terminais SET status = 'bloqueado', motivo_bloqueio = ? WHERE id = ?",
                                (parametros.get("motivo"), terminal_id))
                elif tipo == "desbloquear":
                    con.execute("UPDATE terminais SET status = 'ativo', motivo_bloqueio = NULL WHERE id = ?",
                                (terminal_id,))
                self._auditar(con, "comando_criado", terminal_id=terminal_id, detalhes=tipo)
        log.info("Comando %d para %s: %s %s", comando_id, terminal, tipo, parametros or "")
        return dict(parametros, id=comando_id, tipo=tipo)

    def proximo_comando(self, terminal):
        """A frente da fila: o comando pendente mais antigo (menor id)."""
        with self._trava:
            terminal_id = self._id_terminal(terminal)
            self._registrar_contato(terminal_id)
            linha = self._con.execute(
                "SELECT id, tipo, parametros FROM comandos WHERE terminal_id = ? AND status = 'pendente' "
                "ORDER BY id LIMIT 1", (terminal_id,)).fetchone()
        if linha is None:
            return None
        log.info("Comando %d entregue a %s: %s", linha["id"], terminal, linha["tipo"])
        return dict(json.loads(linha["parametros"]), id=linha["id"], tipo=linha["tipo"])

    def confirmar_comando(self, terminal, comando_id, resultado):
        """Tira o comando da fila. Confirmação repetida não faz nada (idempotente)."""
        with self._trava:
            terminal_id = self._id_terminal(terminal)
            with transacao(self._con) as con:
                alterou = con.execute(
                    "UPDATE comandos SET status = 'executado', executado_em = ?, resultado = ? "
                    "WHERE id = ? AND terminal_id = ? AND status = 'pendente'",
                    (agora(), resultado, comando_id, terminal_id)).rowcount
                if alterou:
                    self._auditar(con, "comando_executado", terminal_id=terminal_id,
                                  detalhes="{}: {}".format(comando_id, resultado))
        if alterou:
            log.info("%s executou o comando %d: %s", terminal, comando_id, resultado)

    # ==================================================================
    # Telemetria
    # ==================================================================

    def registrar_telemetria(self, dados):
        with self._trava:
            terminal_id = self._id_terminal(dados["terminal"])
            self._registrar_contato(terminal_id)
            online = dados.get("online")
            self._con.execute(
                "INSERT INTO telemetria (terminal_id, online, rssi, memoria_livre, fila_pendente, dados) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (terminal_id, None if online is None else int(bool(online)), dados.get("rssi"),
                 dados.get("memoria_livre"), dados.get("fila_pendente"),
                 json.dumps(dados, ensure_ascii=False)))
        log.info("Telemetria de %s: sinal %s dBm, memória livre %s, fila %s.", dados["terminal"],
                 dados.get("rssi"), dados.get("memoria_livre"), dados.get("fila_pendente"))

    # ==================================================================
    # Consultas para o dashboard (Entrega 4)
    # ==================================================================

    @staticmethod
    def _filtros(desde, terminal, prefixo="t", ate=None, campo_data="recebida_em"):
        """Monta o WHERE comum dos relatórios (período e terminal)."""
        condicoes, parametros = ["1 = 1"], []
        if desde:
            condicoes.append("{}.{} >= ?".format(prefixo, campo_data))
            parametros.append(desde)
        if ate:
            condicoes.append("{}.{} < ?".format(prefixo, campo_data))
            parametros.append(ate)
        if terminal:
            condicoes.append("te.codigo = ?")
            parametros.append(terminal)
        return " AND ".join(condicoes), parametros

    def listar_transacoes(self, limite=20, desde=None, terminal=None, ate=None):
        onde, parametros = self._filtros(desde, terminal, ate=ate)
        with self._trava:
            linhas = self._con.execute(
                "SELECT t.id, t.id_transacao, te.codigo AS terminal, t.uid_cartao, cl.nome AS titular, "
                "t.total_centavos, t.status, t.motivo, t.origem, t.recebida_em, "
                "t.criada_no_terminal, t.relogio_sincronizado "
                "FROM transacoes t JOIN terminais te ON te.id = t.terminal_id "
                "LEFT JOIN cartoes c ON c.id = t.cartao_id LEFT JOIN contas ct ON ct.id = c.conta_id "
                "LEFT JOIN clientes cl ON cl.id = ct.cliente_id WHERE " + onde + " ORDER BY t.id DESC LIMIT ?",
                parametros + [limite]).fetchall()
        return [dict(linha) for linha in linhas]

    def listar_telemetria(self, desde=None, terminal=None, limite=5000):
        """Histórico da telemetria (sinal, memória, fila), do mais antigo ao mais novo."""
        onde, parametros = self._filtros(desde, terminal, prefixo="tl", campo_data="registrada_em")
        with self._trava:
            linhas = self._con.execute(
                "SELECT te.codigo AS terminal, tl.registrada_em, tl.online, tl.rssi, tl.memoria_livre, "
                "tl.fila_pendente FROM telemetria tl JOIN terminais te ON te.id = tl.terminal_id "
                "WHERE " + onde + " ORDER BY tl.id DESC LIMIT ?", parametros + [limite]).fetchall()
        return [dict(linha) for linha in reversed(linhas)]

    def mais_vendidos(self, k=5, desde=None, terminal=None):
        """Os k produtos com mais unidades vendidas no período.

        AED: as quantidades são somadas numa TABELA HASH (código -> total,
        O(1) por item vendido) e os k maiores saem de um HEAP de tamanho k
        (heapq.nlargest): O(n log k), sem ordenar a lista inteira, que
        custaria O(n log n). Vendas estornadas não contam.
        """
        onde, parametros = self._filtros(desde, terminal)
        with self._trava:
            linhas = self._con.execute(
                "SELECT p.codigo, p.nome, i.quantidade, i.quantidade * i.preco_unitario_centavos AS receita "
                "FROM itens_transacao i JOIN transacoes t ON t.id = i.transacao_id "
                "JOIN terminais te ON te.id = t.terminal_id JOIN produtos p ON p.id = i.produto_id "
                "WHERE t.status = 'aprovada' AND " + onde, parametros).fetchall()
        totais = TabelaHash()
        for linha in linhas:
            atual = totais.buscar(linha["codigo"]) or {"codigo": linha["codigo"], "nome": linha["nome"],
                                                       "unidades": 0, "receita_centavos": 0}
            atual["unidades"] += linha["quantidade"]
            atual["receita_centavos"] += linha["receita"]
            totais.inserir(linha["codigo"], atual)
        # Empate em unidades: maior receita primeiro; depois, o código (ordem estável)
        return heapq.nlargest(k, (valor for _, valor in totais.itens()),
                              key=lambda p: (p["unidades"], p["receita_centavos"], -int(p["codigo"])))

    def listar_comandos(self, limite=10, terminal=None):
        onde, parametros = self._filtros(None, terminal)
        with self._trava:
            linhas = self._con.execute(
                "SELECT c.id, te.codigo AS terminal, c.tipo, c.parametros, c.status, c.criado_em, "
                "c.executado_em, c.resultado FROM comandos c JOIN terminais te ON te.id = c.terminal_id "
                "WHERE " + onde + " ORDER BY c.id DESC LIMIT ?", parametros + [limite]).fetchall()
        comandos = []
        for linha in linhas:
            comando = dict(linha)
            comando["parametros"] = json.loads(comando["parametros"])
            comandos.append(comando)
        return comandos

    def listar_terminais(self):
        with self._trava:
            linhas = self._con.execute(
                "SELECT te.codigo, te.status, te.motivo_bloqueio, te.ultimo_contato, "
                "(SELECT dados FROM telemetria tl WHERE tl.terminal_id = te.id ORDER BY tl.id DESC LIMIT 1) "
                "AS ultima_telemetria FROM terminais te ORDER BY te.codigo").fetchall()
        terminais = []
        for linha in linhas:
            terminal = dict(linha)
            terminal["ultima_telemetria"] = (json.loads(terminal["ultima_telemetria"])
                                             if terminal["ultima_telemetria"] else None)
            terminais.append(terminal)
        return terminais

    def listar_auditoria(self, limite=50):
        with self._trava:
            linhas = self._con.execute(
                "SELECT a.id, a.criado_em, a.evento, te.codigo AS terminal, a.conta_id, a.transacao_id, "
                "a.valor_anterior, a.valor_novo, a.detalhes FROM logs_auditoria a "
                "LEFT JOIN terminais te ON te.id = a.terminal_id ORDER BY a.id DESC LIMIT ?",
                (limite,)).fetchall()
        return [dict(linha) for linha in linhas]
