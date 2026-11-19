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

-- Senhas de teste (só o hash vai para o banco; gerado com  python -m backend.senhas 1234):
--   cartão branco 1234 · chaveiro azul 4321 · A1B2C3D4 1111 · 11223344 2222 · DEADBEEF 0000
INSERT INTO cartoes (conta_id, uid_rfid, apelido, status, motivo_bloqueio, pin_hash) VALUES
    (1, '532F07FE', 'Cartão branco (bancada)', 'ativo', NULL,
     'pbkdf2_sha256$100000$97b5a7f49ea12fe3391b54c765453b81$c866d52fce1ba9bfe0ae8bb3f92fb5b3972dc183c0e0f9e08fbb40bd8802e557'),
    (2, '93892B4F', 'Chaveiro azul (bancada)', 'ativo', NULL,
     'pbkdf2_sha256$100000$b10cd3c3f4f715d0bea9054f81905929$e9fa0b244d89273420f7ad29dca99e9ff741f33bc487600e8ce459de5d567840'),
    (3, 'A1B2C3D4', 'Cartão de teste (simulador)', 'ativo', NULL,
     'pbkdf2_sha256$100000$6dde319983fe9fc514d9e16fc13613ee$984afcdd591fe1c187ba39872e29e8689fa59d47eeb4584a8b8382ec1228b219'),
    (4, '11223344', 'Cartão de teste (simulador)', 'ativo', NULL,
     'pbkdf2_sha256$100000$96d2a1aff31a69da2e5e5077f75af4ab$2b069939ca253136e7ae1948fe4413de72b5eb3db9206ee03d66f05a336cb593'),
    (5, 'DEADBEEF', 'Cartão bloqueado (teste)', 'bloqueado', 'Bloqueado pelo banco (teste)',
     'pbkdf2_sha256$100000$ba6c576dbd9949fc997f3b198ce62976$9302b12149e2d71cc9a797f091a1137f61521b5163a6d1040e33a2597a53d61e');

INSERT INTO produtos (codigo, nome, preco_centavos, estoque) VALUES
    ('1', 'Café',          500, 50),
    ('2', 'Água',          300, 50),
    ('3', 'Suco',          750, 30),
    ('4', 'Pão de queijo', 450, 40),
    ('5', 'Bolo',          600, 20),
    ('6', 'Sanduíche',    1200, 15),
    ('7', 'Chocolate',     400,  2);   -- estoque baixo: para testar "esgot"

INSERT INTO terminais (codigo) VALUES ('POS-01');
