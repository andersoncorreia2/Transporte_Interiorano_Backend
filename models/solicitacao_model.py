import urllib.parse
from psycopg2.extras import RealDictCursor
from datetime import datetime, timezone, timedelta

def model_listar_e_expirar_solicitacoes(conexao):
    cursor = conexao.cursor(cursor_factory=RealDictCursor)
    try:
        limite_tempo = datetime.now() - timedelta(minutes=15)
        cursor.execute("""
            UPDATE solicitacoes 
            SET status = 'Expirado' 
            WHERE status = 'Pendente' AND data_criacao < %s
        """, (limite_tempo,))
        conexao.commit()
        cursor.execute("SELECT * FROM solicitacoes")
        return cursor.fetchall()
    finally:
        cursor.close()

def model_pedir_carona_fluxo(conexao, carona_id, cpf_passageiro, dados):
    cursor = conexao.cursor(cursor_factory=RealDictCursor)
    try:
        cursor.execute("SELECT vagas, motorista_cpf FROM caronas WHERE id = %s", (carona_id,))
        carona = cursor.fetchone()
        if not carona:
            return None

        cursor.execute("DELETE FROM solicitacoes WHERE carona_id = %s AND passageiro_cpf = %s AND status = 'Expirado'", (carona_id, cpf_passageiro))
        
        cursor.execute("""
            INSERT INTO solicitacoes (carona_id, passageiro, passageiro_cpf, status, data_criacao) 
            VALUES (%s, %s, %s, 'Pendente', %s)
        """, (carona_id, dados["passageiro"], cpf_passageiro, datetime.now()))
        
        cursor.execute("SELECT fcm_token FROM usuarios WHERE cpf = %s", (carona["motorista_cpf"],))
        motorista = cursor.fetchone()
        
        cursor.execute("SELECT fcm_token FROM usuarios WHERE cpf = %s", (cpf_passageiro,))
        passageiro = cursor.fetchone()
        
        conexao.commit()
        return {
            "motorista_token": motorista["fcm_token"] if motorista else None,
            "passageiro_token": passageiro["fcm_token"] if passageiro else None
        }
    finally:
        cursor.close()

def model_cancelar_solicitacao_simples(conexao, id_solicitacao):
    cursor = conexao.cursor(cursor_factory=RealDictCursor)
    try:
        cursor.execute("SELECT id FROM solicitacoes WHERE id = %s", (id_solicitacao,))
        if cursor.fetchone():
            cursor.execute("DELETE FROM solicitacoes WHERE id = %s", (id_solicitacao,))
            conexao.commit()
            return True
        return False
    finally:
        cursor.close()

def model_finalizar_solicitacao_fluxo(conexao, solicitacao_id):
    cursor = conexao.cursor(cursor_factory=RealDictCursor)
    try:
        cursor.execute("""
            UPDATE solicitacoes 
            SET status = 'Finalizada', data_finalizacao = %s 
            WHERE id = %s
        """, (datetime.now(timezone.utc), solicitacao_id))
        
        cursor.execute("""
            SELECT s.passageiro_cpf, c.motorista_cpf, c.vagas, c.id as carona_real_id 
            FROM solicitacoes s JOIN caronas c ON s.carona_id = c.id WHERE s.id = %s
        """, (solicitacao_id,))
        info = cursor.fetchone()
        
        cursor.execute("UPDATE usuarios SET corridas_realizadas = COALESCE(corridas_realizadas, 0) + 1 WHERE cpf = %s", (info['passageiro_cpf'],))
        cursor.execute("UPDATE usuarios SET passageiros_conduzidos = COALESCE(passageiros_conduzidos, 0) + 1 WHERE cpf = %s", (info['motorista_cpf'],))
        
        cursor.execute("SELECT count(*) as count FROM solicitacoes WHERE carona_id = %s AND status IN ('Aceito', 'Pendente')", (info["carona_real_id"],))
        restantes = cursor.fetchone()['count']
        
        if restantes == 0:
            cursor.execute("UPDATE usuarios SET corridas_realizadas = COALESCE(corridas_realizadas, 0) + 1 WHERE cpf = %s", (info['motorista_cpf'],))
            cursor.execute("UPDATE caronas SET status = 'Finalizada' WHERE id = %s", (info["carona_real_id"],))
            vagas_do_evento = int(info['vagas']) if info['vagas'] else 4
            cursor.execute("UPDATE usuarios SET vagas_ofertadas = COALESCE(vagas_ofertadas, 0) + %s WHERE cpf = %s", (vagas_do_evento, info['motorista_cpf']))
            
        conexao.commit()
    finally:
        cursor.close()

def model_listar_historico_passageiro(conexao, cpf):
    cursor = conexao.cursor(cursor_factory=RealDictCursor)
    try:
        cursor.execute("""
            SELECT s.id, s.carona_id, s.passageiro, s.passageiro_cpf, s.status, c.evento_nome, c.cidade_origem, c.cidade_destino,
                   c.horario as horario,
                   to_char(s.data_criacao, 'DD/MM/YYYY HH24:MI') as data_criacao,
                   to_char((s.data_finalizacao AT TIME ZONE 'UTC') AT TIME ZONE 'America/Sao_Paulo', 'DD/MM/YYYY HH24:MI') as data_finalizacao,
                   u_mot.nome as motorista_nome
            FROM solicitacoes s 
            JOIN caronas c ON s.carona_id = c.id 
            LEFT JOIN usuarios u_mot ON c.motorista_cpf = u_mot.cpf
            WHERE s.passageiro_cpf = %s AND s.status = 'Finalizada' 
            ORDER BY s.data_criacao DESC
        """, (urllib.parse.unquote(cpf),))
        return cursor.fetchall()
    finally:
        cursor.close()

def model_listar_historico_motorista(conexao, cpf):
    cursor = conexao.cursor(cursor_factory=RealDictCursor)
    try:
        cursor.execute("""
            SELECT DISTINCT ON (c.id) c.id, c.id as carona_id, s.passageiro as passageiro, s.passageiro_cpf as passageiro_cpf, c.status, c.evento_nome, c.cidade_origem, c.cidade_destino,
                   c.horario as horario,
                   to_char(s.data_criacao, 'DD/MM/YYYY HH24:MI') as data_criacao,
                   to_char((s.data_finalizacao AT TIME ZONE 'UTC') AT TIME ZONE 'America/Sao_Paulo', 'DD/MM/YYYY HH24:MI') as data_finalizacao
            FROM caronas c
            LEFT JOIN solicitacoes s ON s.carona_id = c.id AND s.status = 'Finalizada'
            WHERE c.motorista_cpf = %s AND c.status = 'Finalizada' 
            ORDER BY c.id DESC
        """, (urllib.parse.unquote(cpf),))
        return cursor.fetchall()
    finally:
        cursor.close()

def model_cancelar_carona_geral_fluxo(conexao, carona_id):
    cursor = conexao.cursor(cursor_factory=RealDictCursor)
    try:
        cursor.execute("""
            SELECT u.fcm_token FROM solicitacoes s JOIN usuarios u ON s.passageiro_cpf = u.cpf 
            WHERE s.carona_id = %s AND s.status IN ('Pendente', 'Aceito', 'Aprovado')
        """, (carona_id,))
        passageiros_afetados = cursor.fetchall()

        cursor.execute("SELECT evento_nome FROM caronas WHERE id = %s", (carona_id,))
        evento = cursor.fetchone()
        nome_evento = evento["evento_nome"] if evento else "Viagem"

        return {
            "passageiros": passageiros_afetados,
            "nome_evento": nome_evento
        }
    finally:
        cursor.close()

def model_executar_cancelamento_banco(conexao, carona_id, motivo_cancelamento):
    cursor = conexao.cursor()
    try:
        cursor.execute("UPDATE caronas SET status = 'Cancelada' WHERE id = %s", (carona_id,))
        cursor.execute("UPDATE solicitacoes SET status = %s WHERE carona_id = %s AND status != 'Finalizada'", (f"Cancelado: {motivo_cancelamento}", carona_id))
        conexao.commit()
    finally:
        cursor.close()
        
def model_atualizar_status_solicitacao(conexao, id_solicitacao, novo_status):
    cursor = conexao.cursor(cursor_factory=RealDictCursor)
    try:
        cursor.execute("SELECT status, carona_id FROM solicitacoes WHERE id = %s", (id_solicitacao,))
        status_antigo_reg = cursor.fetchone()
        
        if not status_antigo_reg:
            return None
            
        status_antigo = status_antigo_reg["status"]

        if status_antigo == novo_status:
            cursor.execute("""
                SELECT u_pass.fcm_token as passageiro_token, c.evento_nome 
                FROM solicitacoes s
                JOIN usuarios u_pass ON s.passageiro_cpf = u_pass.cpf
                JOIN caronas c ON s.carona_id = c.id
                WHERE s.id = %s
            """, (id_solicitacao,))
            return cursor.fetchone()

        cursor.execute("UPDATE solicitacoes SET status = %s WHERE id = %s", (novo_status, id_solicitacao))
                
        cursor.execute("""
            SELECT u_pass.fcm_token as passageiro_token, c.evento_nome 
            FROM solicitacoes s
            JOIN usuarios u_pass ON s.passageiro_cpf = u_pass.cpf
            JOIN caronas c ON s.carona_id = c.id
            WHERE s.id = %s
        """, (id_solicitacao,))
        info = cursor.fetchone()
        
        conexao.commit()
        return info
    finally:
        cursor.close()