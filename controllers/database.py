# database.py
import os
import psycopg2
from psycopg2.extras import RealDictCursor

def conectar_banco():
    DATABASE_URL = os.environ.get("DATABASE_URL")
    
    if not DATABASE_URL:
        print("⚠️ AVISO: A variável de ambiente 'DATABASE_URL' não foi localizada!")
        return None
        
    try:
        conexao = psycopg2.connect(DATABASE_URL)
        return conexao
    except Exception as e:
        print(f"Erro ao conectar no banco: {e}")
        return None

def inicializar_banco():
    """Cria as tabelas necessárias caso elas ainda não existam no banco."""
    conexao = conectar_banco()
    if not conexao:
        print("❌ Não foi possível inicializar o banco de dados.")
        return

    cursor = None
    try:
        cursor = conexao.cursor()
        
        # Criação da tabela de débitos e pagamentos pendentes
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS debitos_passageiros (
                id SERIAL PRIMARY KEY,
                passageiro_cpf VARCHAR(14) NOT NULL,
                corrida_id INT,
                valor_pendente NUMERIC(10,2) DEFAULT 0.00,
                valor_cobrado NUMERIC(10,2) DEFAULT 0.01,
                payment_id VARCHAR(100),
                status VARCHAR(20) DEFAULT 'pendente',
                    data_criacao TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)
        
        conexao.commit()
        print("🟢 Tabelas e estruturas verificadas/criadas com sucesso!")
        
    except Exception as e:
        if conexao:
            conexao.rollback()
        print(f"❌ Erro ao criar tabelas no banco: {e}")
    finally:
        if cursor:
            cursor.close()
        if conexao:
            conexao.close()