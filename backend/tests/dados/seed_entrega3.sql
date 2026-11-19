-- =====================================================================
-- seed.sql - Carga inicial de teste do ByteBank Pay
-- =====================================================================
-- Executado depois do schema.sql. CPFs fictícios (não são de ninguém).
-- Os dois primeiros cartões são os da bancada, lidos em 05/11 com
-- esp32/exemplos/ler_cartao.py.
-- =====================================================================

INSERT INTO clientes (id, nome, cpf) VALUES
    (1, 'Cliente do cartão branco',  '00000000001'),
    (2, 'Cliente do chaveiro azul',  '00000000002'),
    (3, 'Amanda Silva',              '00000000003'),
    (4, 'Caio Ferreira',             '00000000004'),
    (5, 'Douglas Alves',             '00000000005');

INSERT INTO contas (id, cliente_id, saldo_centavos) VALUES
    (1, 1, 100000),   -- R$ 1.000,00
    (2, 2,    500),   -- R$ 5,00: para testar "Saldo insuficiente"
    (3, 3, 100000),
    (4, 4,   1000),
    (5, 5,  50000);

INSERT INTO cartoes (conta_id, uid_rfid, apelido, status) VALUES
    (1, '532F07FE', 'Cartão branco (bancada)',  'ativo'),
    (2, '93892B4F', 'Chaveiro azul (bancada)',  'ativo'),
    (3, 'A1B2C3D4', 'Cartão de teste (simulador)', 'ativo'),
    (4, '11223344', 'Cartão de teste (simulador)', 'ativo'),
    (5, 'DEADBEEF', 'Cartão bloqueado (teste)',  'bloqueado');

INSERT INTO produtos (codigo, nome, preco_centavos, estoque) VALUES
    ('1', 'Café',          500, 50),
    ('2', 'Água',          300, 50),
    ('3', 'Suco',          750, 30),
    ('4', 'Pão de queijo', 450, 40),
    ('5', 'Bolo',          600, 20),
    ('6', 'Sanduíche',    1200, 15),
    ('7', 'Chocolate',     400,  2);   -- estoque baixo: para testar "esgot"

INSERT INTO terminais (codigo) VALUES ('POS-01');
