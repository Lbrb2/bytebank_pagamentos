-- =====================================================================
-- schema.sql - Modelo relacional do ByteBank Pay (SQLite)
-- =====================================================================
--
-- Cria todas as tabelas do zero. O backend executa este arquivo sozinho
-- na primeira vez que roda (ou com "python -m backend --recriar").
-- Para criar o banco à mão:
--
--     sqlite3 database/bytebank.db < database/schema.sql
--     sqlite3 database/bytebank.db < database/seed.sql
--
-- Regras gerais:
--   * dinheiro sempre em CENTAVOS (INTEGER): 1300 = R$ 13,00. Somar números
--     com casas decimais acumula erro de arredondamento; inteiros, não;
--   * datas em texto ISO 8601, em UTC (ex.: 2026-11-12T14:03:10Z);
--   * as regras mais importantes ficam também no banco (CHECK, UNIQUE,
--     FOREIGN KEY, gatilhos). Mesmo que o código tenha um erro, o banco
--     recusa um saldo negativo ou uma venda registrada duas vezes.
-- =====================================================================

PRAGMA foreign_keys = ON;

-- ---------------------------------------------------------------------
-- Clientes, contas e cartões
-- ---------------------------------------------------------------------

CREATE TABLE clientes (
    id          INTEGER PRIMARY KEY,
    nome        TEXT    NOT NULL CHECK (length(trim(nome)) > 0),
    cpf         TEXT    NOT NULL UNIQUE CHECK (length(cpf) = 11 AND cpf NOT GLOB '*[^0-9]*'),
    criado_em   TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

-- Saldo nunca negativo: o CHECK é a última barreira, mesmo contra um UPDATE manual.
CREATE TABLE contas (
    id              INTEGER PRIMARY KEY,
    cliente_id      INTEGER NOT NULL REFERENCES clientes (id),
    saldo_centavos  INTEGER NOT NULL DEFAULT 0 CHECK (saldo_centavos >= 0),
    criada_em       TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

-- O UID é o número de série gravado no chip do cartão RFID (8 dígitos hexadecimais).
CREATE TABLE cartoes (
    id          INTEGER PRIMARY KEY,
    conta_id    INTEGER NOT NULL REFERENCES contas (id),
    uid_rfid    TEXT    NOT NULL UNIQUE CHECK (uid_rfid = upper(uid_rfid) AND length(uid_rfid) >= 8),
    apelido     TEXT,
    status      TEXT    NOT NULL DEFAULT 'ativo' CHECK (status IN ('ativo', 'bloqueado'))
);

-- ---------------------------------------------------------------------
-- Catálogo e terminais
-- ---------------------------------------------------------------------

-- O código é a posição do produto no terminal: um dígito de 1 a 9.
CREATE TABLE produtos (
    id              INTEGER PRIMARY KEY,
    codigo          TEXT    NOT NULL UNIQUE CHECK (codigo IN ('1','2','3','4','5','6','7','8','9')),
    nome            TEXT    NOT NULL CHECK (length(trim(nome)) > 0),
    preco_centavos  INTEGER NOT NULL CHECK (preco_centavos > 0),
    estoque         INTEGER NOT NULL DEFAULT 0 CHECK (estoque >= 0)
);

CREATE TABLE terminais (
    id               INTEGER PRIMARY KEY,
    codigo           TEXT    NOT NULL UNIQUE,              -- ex.: POS-01 (TERMINAL_ID do config.py)
    status           TEXT    NOT NULL DEFAULT 'ativo' CHECK (status IN ('ativo', 'bloqueado')),
    motivo_bloqueio  TEXT,
    ultimo_contato   TEXT
);

-- ---------------------------------------------------------------------
-- Vendas
-- ---------------------------------------------------------------------

-- Toda venda que chega bem formada fica registrada, aprovada ou recusada.
-- id_transacao é gerado pelo terminal e é UNIQUE: se o terminal reenviar a
-- mesma venda (resposta perdida, fila offline), o banco não aceita duas
-- linhas, e o backend devolve a resposta guardada em "resposta" sem cobrar
-- de novo (idempotência).
CREATE TABLE transacoes (
    id                    INTEGER PRIMARY KEY,
    id_transacao          TEXT    NOT NULL UNIQUE,
    terminal_id           INTEGER NOT NULL REFERENCES terminais (id),
    cartao_id             INTEGER REFERENCES cartoes (id),      -- NULL: cartão não cadastrado
    uid_cartao            TEXT    NOT NULL,
    total_centavos        INTEGER NOT NULL DEFAULT 0 CHECK (total_centavos >= 0),
    status                TEXT    NOT NULL CHECK (status IN ('aprovada', 'recusada', 'estornada')),
    motivo                TEXT,                                 -- motivo da recusa
    origem                TEXT    NOT NULL DEFAULT 'online' CHECK (origem IN ('online', 'contingencia')),
    criada_no_terminal    TEXT,                                 -- relógio do ESP32
    relogio_sincronizado  INTEGER NOT NULL DEFAULT 0 CHECK (relogio_sincronizado IN (0, 1)),
    recebida_em           TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    codigo_http           INTEGER NOT NULL,
    resposta              TEXT    NOT NULL                      -- JSON enviado ao terminal
);

-- O preço unitário é COPIADO no momento da venda: se o preço do produto
-- mudar depois, o histórico continua mostrando quanto foi cobrado.
CREATE TABLE itens_transacao (
    id                       INTEGER PRIMARY KEY,
    transacao_id             INTEGER NOT NULL REFERENCES transacoes (id),
    produto_id               INTEGER NOT NULL REFERENCES produtos (id),
    quantidade               INTEGER NOT NULL CHECK (quantidade > 0),
    preco_unitario_centavos  INTEGER NOT NULL CHECK (preco_unitario_centavos > 0),
    UNIQUE (transacao_id, produto_id)
);

-- Uma venda só pode ser estornada uma vez (UNIQUE em transacao_id).
CREATE TABLE estornos (
    id              INTEGER PRIMARY KEY,
    transacao_id    INTEGER NOT NULL UNIQUE REFERENCES transacoes (id),
    terminal_id     INTEGER NOT NULL REFERENCES terminais (id),
    valor_centavos  INTEGER NOT NULL CHECK (valor_centavos > 0),
    criado_em       TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

-- ---------------------------------------------------------------------
-- Comunicação com o terminal
-- ---------------------------------------------------------------------

-- Fila de comandos do gerente para cada terminal. A ordem de execução é a
-- ordem de criação (ORDER BY id): o primeiro pendente é a frente da fila.
CREATE TABLE comandos (
    id            INTEGER PRIMARY KEY,
    terminal_id   INTEGER NOT NULL REFERENCES terminais (id),
    tipo          TEXT    NOT NULL CHECK (tipo IN ('atualizar_catalogo', 'bloquear', 'desbloquear', 'mensagem')),
    parametros    TEXT    NOT NULL DEFAULT '{}',               -- JSON (motivo, texto, segundos)
    status        TEXT    NOT NULL DEFAULT 'pendente' CHECK (status IN ('pendente', 'executado')),
    criado_em     TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    executado_em  TEXT,
    resultado     TEXT
);

CREATE TABLE telemetria (
    id              INTEGER PRIMARY KEY,
    terminal_id     INTEGER NOT NULL REFERENCES terminais (id),
    registrada_em   TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    online          INTEGER CHECK (online IN (0, 1)),
    rssi            INTEGER,                                    -- sinal do Wi-Fi, em dBm
    memoria_livre   INTEGER,                                    -- bytes livres na placa
    fila_pendente   INTEGER,                                    -- vendas offline esperando envio
    dados           TEXT    NOT NULL                            -- pacote JSON completo
);

-- ---------------------------------------------------------------------
-- Auditoria
-- ---------------------------------------------------------------------

-- Registro de tudo o que mexe em dinheiro, estoque ou terminal. Só aceita
-- INSERT: os gatilhos abaixo impedem alterar ou apagar um registro.
CREATE TABLE logs_auditoria (
    id              INTEGER PRIMARY KEY,
    criado_em       TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    evento          TEXT    NOT NULL,
    terminal_id     INTEGER REFERENCES terminais (id),
    conta_id        INTEGER REFERENCES contas (id),
    transacao_id    INTEGER REFERENCES transacoes (id),
    valor_anterior  INTEGER,
    valor_novo      INTEGER,
    detalhes        TEXT
);

-- ---------------------------------------------------------------------
-- Índices (as buscas frequentes não percorrem a tabela inteira)
-- ---------------------------------------------------------------------

CREATE INDEX idx_transacoes_terminal  ON transacoes (terminal_id, recebida_em);
CREATE INDEX idx_transacoes_cartao    ON transacoes (cartao_id);
CREATE INDEX idx_itens_transacao      ON itens_transacao (transacao_id);
CREATE INDEX idx_comandos_pendentes   ON comandos (terminal_id, status, id);
CREATE INDEX idx_telemetria_terminal  ON telemetria (terminal_id, registrada_em);
CREATE INDEX idx_auditoria_data       ON logs_auditoria (criado_em);

-- ---------------------------------------------------------------------
-- Gatilhos de auditoria
-- ---------------------------------------------------------------------
-- Rodam DENTRO da mesma transação do UPDATE: se a venda for desfeita
-- (ROLLBACK), o registro de auditoria some junto. E não dependem do
-- código Python: até um UPDATE feito à mão no banco fica registrado.

CREATE TRIGGER trg_auditoria_saldo
AFTER UPDATE OF saldo_centavos ON contas
WHEN OLD.saldo_centavos <> NEW.saldo_centavos
BEGIN
    INSERT INTO logs_auditoria (evento, conta_id, valor_anterior, valor_novo)
    VALUES ('saldo_alterado', NEW.id, OLD.saldo_centavos, NEW.saldo_centavos);
END;

CREATE TRIGGER trg_auditoria_estoque
AFTER UPDATE OF estoque ON produtos
WHEN OLD.estoque <> NEW.estoque
BEGIN
    INSERT INTO logs_auditoria (evento, valor_anterior, valor_novo, detalhes)
    VALUES ('estoque_alterado', OLD.estoque, NEW.estoque, 'produto ' || NEW.codigo || ' (' || NEW.nome || ')');
END;

CREATE TRIGGER trg_auditoria_preco
AFTER UPDATE OF preco_centavos ON produtos
WHEN OLD.preco_centavos <> NEW.preco_centavos
BEGIN
    INSERT INTO logs_auditoria (evento, valor_anterior, valor_novo, detalhes)
    VALUES ('preco_alterado', OLD.preco_centavos, NEW.preco_centavos, 'produto ' || NEW.codigo || ' (' || NEW.nome || ')');
END;

CREATE TRIGGER trg_auditoria_sem_alteracao
BEFORE UPDATE ON logs_auditoria
BEGIN
    SELECT RAISE(ABORT, 'logs de auditoria não podem ser alterados');
END;

CREATE TRIGGER trg_auditoria_sem_exclusao
BEFORE DELETE ON logs_auditoria
BEGIN
    SELECT RAISE(ABORT, 'logs de auditoria não podem ser apagados');
END;

-- ---------------------------------------------------------------------
-- Visão usada pelo resumo do caixa (modo operador) e pelo dashboard
-- ---------------------------------------------------------------------

CREATE VIEW vw_resumo_caixa AS
SELECT
    te.codigo                                                          AS terminal,
    COALESCE(SUM(t.status = 'aprovada'), 0)                            AS vendas,
    COALESCE(SUM(CASE WHEN t.status = 'aprovada'  THEN t.total_centavos END), 0) AS total_centavos,
    COALESCE(SUM(t.status = 'estornada'), 0)                           AS estornos,
    COALESCE(SUM(CASE WHEN t.status = 'estornada' THEN t.total_centavos END), 0) AS estornado_centavos,
    COALESCE(SUM(t.status = 'recusada'), 0)                            AS recusadas
FROM terminais te
LEFT JOIN transacoes t ON t.terminal_id = te.id
GROUP BY te.id;
