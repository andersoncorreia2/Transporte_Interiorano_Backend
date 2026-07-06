import urllib.parse
from psycopg2.extras import RealDictCursor

def model_listar_caronas(conexao, cpf_passageiro):
    cursor = conexao.cursor(cursor_factory=RealDictCursor)
    try:
        query = """
            SELECT c.* FROM caronas c
            WHERE c.status = 'Aberta'
            AND NOT EXISTS (
                SELECT 1 
                FROM solicitacoes s 
                WHERE s.carona_id = c.id 
                AND s.passageiro_cpf = %s 
                AND s.status LIKE 'Recusado%%'
            )
            ORDER BY c.id DESC
        """
        cpf_real = urllib.parse.unquote(cpf_passageiro)
        cursor.execute(query, (cpf_real,))
        return cursor.fetchall()
    finally:
        cursor.close()

def model_criar_carona(conexao, dados):
    cursor = conexao.cursor()
    try:
        cursor.execute("""
            INSERT INTO caronas (evento_nome, cidade_origem, endereco_origem, cidade_destino, endereco_destino, horario, vagas, motorista, motorista_cpf)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        """, (dados["evento_nome"], dados["cidade_origem"], dados["endereco_origem"], 
              dados["cidade_destino"], dados["endereco_destino"], dados["horario"], 
              dados["vagas"], dados["motorista"], dados["motorista_cpf"]))
        conexao.commit()
    finally:
        cursor.close()

def model_deletar_carona(conexao, id_carona):
    cursor = conexao.cursor()
    try:
        cursor.execute("DELETE FROM solicitacoes WHERE carona_id = %s", (id_carona,))
        cursor.execute("DELETE FROM caronas WHERE id = %s", (id_carona,))
        conexao.commit()
    finally:
        cursor.close()