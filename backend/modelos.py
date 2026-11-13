"""
modelos.py - Formato dos pacotes JSON aceitos pela API (validação)

O FastAPI confere cada requisição contra estes modelos ANTES de chamar a
regra de negócio. Pacote com campo faltando, tipo errado ou valor fora da
faixa é recusado com HTTP 400 e um motivo curto, que o terminal mostra na
tela (ver app.py). Campos extras são ignorados.
"""

from typing import List, Literal, Optional

from pydantic import BaseModel, Field, StrictBool, StrictInt, field_validator, model_validator

PADRAO_TERMINAL = r"^[A-Za-z0-9_-]{1,32}$"   # ex.: POS-01
PADRAO_UID = r"^[0-9A-Fa-f]{8,20}$"          # UID do cartão RFID em hexadecimal


class ItemVenda(BaseModel):
    codigo: str = Field(pattern=r"^[1-9]$", description="tecla do produto, de 1 a 9")
    quantidade: StrictInt = Field(ge=1, le=30)


class Venda(BaseModel):
    """POST /transacoes - pacote enviado pelo ESP32 a cada pagamento."""
    id_transacao: str = Field(min_length=1, max_length=64)
    terminal: str = Field(pattern=PADRAO_TERMINAL)
    uid_cartao: str = Field(pattern=PADRAO_UID)
    itens: List[ItemVenda] = Field(min_length=1, max_length=30)
    total_centavos: Optional[StrictInt] = Field(default=None, ge=0,
                                                description="só informativo: o servidor recalcula")
    criada_em: Optional[str] = Field(default=None, max_length=32)
    relogio_sincronizado: StrictBool = False
    origem: Literal["online", "contingencia"] = "online"

    @field_validator("uid_cartao")
    @classmethod
    def uid_em_maiusculas(cls, uid):
        return uid.upper()


class PedidoEstorno(BaseModel):
    """POST /estornos - modo operador cancelando a última venda."""
    id_transacao: str = Field(min_length=1, max_length=64)
    terminal: str = Field(pattern=PADRAO_TERMINAL)


class Telemetria(BaseModel):
    """POST /telemetria - estado do terminal, a cada 60 s. Outros campos
    (versão, origem do catálogo...) são guardados como vieram."""
    model_config = {"extra": "allow"}
    terminal: str = Field(pattern=PADRAO_TERMINAL)
    online: Optional[bool] = None
    rssi: Optional[int] = None
    memoria_livre: Optional[int] = None
    fila_pendente: Optional[int] = None


class NovoComando(BaseModel):
    """POST /terminais/{terminal}/comandos - enviado pelo gerente."""
    tipo: Literal["atualizar_catalogo", "bloquear", "desbloquear", "mensagem"]
    motivo: Optional[str] = Field(default=None, max_length=96)
    texto: Optional[str] = Field(default=None, max_length=96)
    segundos: int = Field(default=10, ge=3, le=60)

    @model_validator(mode="after")
    def mensagem_precisa_de_texto(self):
        if self.tipo == "mensagem" and not (self.texto or "").strip():
            raise ValueError("o comando mensagem precisa de texto")
        return self

    def parametros(self):
        if self.tipo == "bloquear":
            return {"motivo": self.motivo or "Fale com o gerente"}
        if self.tipo == "mensagem":
            return {"texto": self.texto, "segundos": self.segundos}
        return {}


class Confirmacao(BaseModel):
    resultado: Optional[str] = Field(default=None, max_length=200)


class AlteracaoProduto(BaseModel):
    """PATCH /produtos/{codigo} - gerente muda preço e/ou estoque."""
    preco_centavos: Optional[StrictInt] = Field(default=None, ge=1)
    estoque: Optional[StrictInt] = Field(default=None, ge=0)

    @model_validator(mode="after")
    def pelo_menos_um_campo(self):
        if self.preco_centavos is None and self.estoque is None:
            raise ValueError("informe preco_centavos e/ou estoque")
        return self
