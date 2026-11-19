"""
app.py - API do ByteBank Pay (FastAPI)

As rotas usadas pelo terminal são exatamente as do servidor de teste da
Entrega 2: o código do ESP32 não muda, só o API_URL do config.py.

Para rodar, da pasta raiz do repositório:

    pip install -r backend/requirements.txt
    python -m backend

Com o servidor no ar, http://localhost:8000/docs mostra uma página onde dá
para testar cada rota pelo navegador, sem a placa.
"""

import logging
import os
from typing import Literal, Optional

from fastapi import FastAPI, Query
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response

from . import banco, relatorios
from .modelos import AlteracaoProduto, Confirmacao, NovoComando, PedidoEstorno, Reposicao, Telemetria, Venda
from .servicos import Recusa, Servico

log = logging.getLogger("bytebank")

VERSAO = "3.0 (Entrega 3)"


def criar_app(caminho_banco=None):
    caminho = caminho_banco or os.environ.get("BYTEBANK_DB") or banco.CAMINHO_PADRAO
    servico = Servico(banco.conectar(caminho))

    app = FastAPI(
        title="ByteBank Pay - API",
        version=VERSAO,
        description="Backend do terminal POS: catálogo, vendas, estornos, comandos remotos e telemetria.",
    )
    app.state.servico = servico

    # ---------------- Erros ----------------

    @app.exception_handler(RequestValidationError)
    async def pacote_invalido(request, erro):
        """Pacote fora do formato: HTTP 400 com um motivo curto (o terminal
        mostra o "motivo" na tela; 4xx não volta para a fila offline)."""
        primeiro = erro.errors()[0]
        campo = ".".join(str(parte) for parte in primeiro.get("loc", ()) if parte != "body")
        motivo = "Pacote inválido: {}".format(campo or "JSON")
        log.warning("%s %s recusado: %s (%s)", request.method, request.url.path, motivo, primeiro.get("msg"))
        return JSONResponse(status_code=400, content={
            "status": "recusada", "motivo": motivo,
            "erros": [{"campo": ".".join(str(p) for p in e.get("loc", ())), "erro": e.get("msg")}
                      for e in erro.errors()],
        })

    @app.exception_handler(Recusa)
    async def recusa(request, erro):
        return JSONResponse(status_code=erro.codigo_http, content={"status": "recusado", "motivo": erro.motivo})

    # ---------------- Rotas usadas pelo terminal ----------------

    @app.get("/", tags=["geral"])
    def inicio():
        return {"servico": "ByteBank Pay - API", "versao": VERSAO, "ok": True}

    @app.get("/produtos", tags=["terminal"])
    def produtos():
        """Catálogo com preço e estoque (o terminal mostra "esgot" com estoque 0)."""
        return {"produtos": servico.catalogo()}

    @app.post("/transacoes", tags=["terminal"])
    def transacoes(venda: Venda):
        """Venda: valida cartão e itens, recalcula o total e debita de forma atômica.
        200 aprovada | 422 recusada (com motivo) | 400 pacote inválido."""
        codigo, resposta = servico.processar_venda(venda.model_dump())
        return JSONResponse(status_code=codigo, content=resposta)

    @app.post("/estornos", tags=["terminal"])
    def estornos(pedido: PedidoEstorno):
        """Cancela uma venda aprovada (modo operador). Idempotente."""
        codigo, resposta = servico.estornar(pedido.id_transacao, pedido.terminal)
        return JSONResponse(status_code=codigo, content=resposta)

    @app.get("/terminais/{terminal}/resumo", tags=["terminal"])
    def resumo(terminal: str):
        """Resumo do caixa do terminal (modo operador)."""
        return servico.resumo_do_caixa(terminal)

    @app.get("/terminais/{terminal}/comandos", tags=["terminal"])
    def proximo_comando(terminal: str):
        """Próximo comando pendente da fila do terminal, ou null."""
        return {"comando": servico.proximo_comando(terminal)}

    @app.post("/terminais/{terminal}/comandos/{comando_id}/confirmacao", tags=["terminal"])
    def confirmar(terminal: str, comando_id: int, confirmacao: Confirmacao):
        servico.confirmar_comando(terminal, comando_id, confirmacao.resultado)
        return {"ok": True}

    @app.post("/telemetria", tags=["terminal"])
    def telemetria(dados: Telemetria):
        servico.registrar_telemetria(dados.model_dump())
        return {"ok": True}

    # ---------------- Rotas do gerente (dashboard na Entrega 4) ----------------

    @app.post("/terminais/{terminal}/comandos", status_code=201, tags=["gerente"])
    def criar_comando(terminal: str, comando: NovoComando):
        """Coloca um comando no fim da fila do terminal."""
        return servico.criar_comando(terminal, comando.tipo, comando.parametros())

    @app.patch("/produtos/{codigo}", tags=["gerente"])
    def alterar_produto(codigo: str, alteracao: AlteracaoProduto):
        """Muda o preço e/ou corrige a contagem do estoque. Com estoque_esperado,
        responde 409 se uma venda mudou o estoque desde que o gerente olhou."""
        return servico.alterar_produto(codigo, alteracao.preco_centavos, alteracao.estoque,
                                       alteracao.estoque_esperado)

    @app.post("/produtos/{codigo}/reposicao", tags=["gerente"])
    def repor_estoque(codigo: str, reposicao: Reposicao):
        """Chegou mercadoria: soma as unidades ao estoque atual."""
        return servico.repor_estoque(codigo, reposicao.unidades)

    @app.get("/transacoes", tags=["gerente"])
    def listar_transacoes(limite: int = Query(20, ge=1, le=5000),
                          desde: Optional[str] = Query(None, description="ISO 8601 em UTC, ex. 2026-11-19T03:00:00Z"),
                          ate: Optional[str] = Query(None, description="fim do período (exclusivo), ISO 8601 em UTC"),
                          terminal: Optional[str] = None):
        """Últimas vendas (aprovadas, recusadas e estornadas), da mais nova para a mais antiga."""
        return {"transacoes": servico.listar_transacoes(limite, desde, terminal, ate)}

    @app.get("/telemetria", tags=["gerente"])
    def historico_telemetria(desde: Optional[str] = None, terminal: Optional[str] = None,
                             limite: int = Query(5000, ge=1, le=50000)):
        """Histórico de sinal do Wi-Fi, memória livre e fila offline de cada terminal."""
        return {"telemetria": servico.listar_telemetria(desde, terminal, limite)}

    @app.get("/relatorios/mais-vendidos", tags=["gerente"])
    def mais_vendidos(k: int = Query(5, ge=1, le=9), desde: Optional[str] = None,
                      terminal: Optional[str] = None):
        """Top k produtos por unidades vendidas (tabela hash + heap)."""
        return {"produtos": servico.mais_vendidos(k, desde, terminal)}

    @app.get("/cartoes/bloqueados", tags=["gerente"])
    def cartoes_bloqueados():
        """Cartões bloqueados, com o motivo (inclui os bloqueados por suspeita de fraude)."""
        return {"cartoes": servico.cartoes_bloqueados()}

    @app.post("/cartoes/{uid}/desbloquear", tags=["gerente"])
    def desbloquear_cartao(uid: str):
        """O gerente libera um cartão bloqueado (por exemplo, depois de falar com o cliente)."""
        return servico.desbloquear_cartao(uid)

    @app.get("/relatorios/exportar", tags=["gerente"])
    def exportar(formato: Literal["xlsx", "csv"] = "xlsx", desde: Optional[str] = None,
                 terminal: Optional[str] = None):
        """Relatório do período para baixar: Excel (5 abas) ou CSV (transações)."""
        dados = relatorios.montar(servico, desde, terminal)
        if formato == "csv":
            conteudo, tipo = relatorios.para_csv(dados), "text/csv; charset=utf-8"
        else:
            conteudo = relatorios.para_excel(dados)
            tipo = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        nome = relatorios.nome_do_arquivo(formato, terminal)
        log.info("Relatório %s gerado: %d transações.", nome, len(dados["transacoes"]))
        return Response(conteudo, media_type=tipo,
                        headers={"Content-Disposition": 'attachment; filename="{}"'.format(nome)})

    @app.get("/comandos", tags=["gerente"])
    def listar_comandos(limite: int = Query(10, ge=1, le=200), terminal: Optional[str] = None):
        """Últimos comandos enviados, com a situação (pendente ou executado)."""
        return {"comandos": servico.listar_comandos(limite, terminal)}

    @app.get("/terminais", tags=["gerente"])
    def listar_terminais():
        """Terminais, situação e última telemetria."""
        return {"terminais": servico.listar_terminais()}

    @app.get("/auditoria", tags=["gerente"])
    def listar_auditoria(limite: int = Query(50, ge=1, le=500)):
        return {"auditoria": servico.listar_auditoria(limite)}

    @app.get("/diagnostico/tabelas-hash", tags=["geral"])
    def tabelas_hash():
        """Tamanho, fator de carga e colisões de cada tabela hash."""
        return servico.estatisticas_tabelas_hash()

    return app
