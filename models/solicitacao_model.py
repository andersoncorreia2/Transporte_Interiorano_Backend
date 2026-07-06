import urllib.parse
from psycopg2.extras import RealDictCursor
from datetime import datetime, timezone, timedelta

def model_listar_e_expirar_solicitacoes(conexao):
    cursor = conexao.cursor(cursor_factory=RealDictCursor)
    agora = datetime.now(timezone.utc)
    try:
        # Verifica se alguma solicitação estourou o tempo de 15 minutos
        cursor.execute("SELECT id, data_criacao FROM solicitacoes WHERE status = 'Pendente'")
        pendentes = cursor.fetchall()
        for sol in pendentes:
            data_criacao = sol["data_criacao"]
            if data_criacao and data_criacao.tzinfo is None:
                data_criacao = data_criacao.replace(tzinfo=timezone.utc)
            if data_criacao and (agora - data_criacao) > timedelta(minutes=15):
                cursor.execute("UPDATE solicitacoes SET status = 'Expirado' WHERE id = %s", (sol["id"],))
        conexao.commit()

        # Retorna a lista completa atualizada
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

        # Limpa pedidos expirados anteriores desse passageiro nessa carona
        cursor.execute("DELETE FROM solicitacoes WHERE carona_id = %s AND passageiro_cpf = %s AND status = 'Expirado'", (carona_id, cpf_passageiro))
        
        # Insere o novo pedido pendente
        cursor.execute("""
            INSERT INTO solicitacoes (carona_id, passageiro, passageiro_cpf, status, data_criacao) 
            VALUES (%s, %s, %s, 'Pendente', %s)
        """, (carona_id, dados["passageiro"], cpf_passageiro, datetime.now(timezone.utc)))
        
        # Busca tokens FCM para as notificações
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
        cursor.execute("UPDATE solicitacoes SET status = 'Finalizado' WHERE id = %s", (solicitacao_id,))
        cursor.execute("""
            SELECT s.passageiro_cpf, c.motorista_cpf, c.vagas, c.id as carona_real_id 
            FROM solicitacoes s JOIN caronas c ON s.carona_id = c.id WHERE s.id = %s
        """, (solicitacao_id,))
        info = cursor.fetchone()
        
        # Incrementa pontuações de corridas
        cursor.execute("UPDATE usuarios SET corridas_realizadas = COALESCE(corridas_realizadas, 0) + 1 WHERE cpf = %s", (info['passageiro_cpf'],))
        cursor.execute("UPDATE usuarios SET passageiros_conduzidos = COALESCE(passageiros_conduzidos, 0) + 1 WHERE cpf = %s", (info['motorista_cpf'],))
        
        # Se não houver mais passageiros pendentes/aceitos nessa carona, finaliza o evento inteiro
        cursor.execute("SELECT count(*) as count FROM solicitacoes WHERE carona_id = %s AND status != 'Finalizado'", (info["carona_real_id"],))
        restantes = cursor.fetchone()['count']
        
        if restantes == 0:
            cursor.execute("UPDATE usuarios SET corridas_realizadas = COALESCE(corridas_realizadas, 0) + 1 WHERE cpf = %s", (info['motorista_cpf'],))
            cursor.execute("UPDATE caronas SET status = 'Finalizado' WHERE id = %s", (info["carona_real_id"],))
            vagas_do_evento = int(info['vagas']) if info['vagas'] else 4
            cursor.execute("UPDATE usuarios SET vagas_ofertadas = COALESCE(vagas_ofertadas, 0) + %s WHERE cpf = %s", (vagas_do_evento, info['motorista_cpf']))
            
        conexao.commit()
    finally:
        cursor.close()

def model_listar_historico_passageiro(conexao, cpf):
    cursor = conexao.cursor(cursor_factory=RealDictCursor)
    try:
        cursor.execute("""
            SELECT s.id, s.carona_id, s.passageiro, s.passageiro_cpf, s.status, c.evento_nome, c.cidade_origem, c.cidade_destino, c.horario
            FROM solicitacoes s JOIN caronas c ON s.carona_id = c.id WHERE s.passageiro_cpf = %s AND s.status = 'Finalizado' ORDER BY s.data_criacao DESC
        """, (urllib.parse.unquote(cpf),))
        return cursor.fetchall()
    finally:
        cursor.close()

def model_listar_historico_motorista(conexao, cpf):
    cursor = conexao.cursor(cursor_factory=RealDictCursor)
    try:
        cursor.execute("""
            SELECT DISTINCT ON (c.id) c.id, c.id as carona_id, c.motorista as passageiro, c.motorista_cpf as passageiro_cpf, c.status, c.evento_nome, c.cidade_origem, c.cidade_destino, c.horario
            FROM caronas c WHERE c.motorista_cpf = %s AND c.status = 'Finalizado' ORDER BY c.id DESC
        """, (urllib.parse.unquote(cpf),))
        return cursor.fetchall()
    finally:
        cursor.close()

def model_cancelar_carona_geral_fluxo(conexao, carona_id):
    cursor = conexao.cursor(cursor_factory=RealDictCursor)
    try:
        # Busca passageiros afetados para notificação fcm
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
        cursor.execute("UPDATE solicitacoes SET status = %s WHERE carona_id = %s AND status != 'Finalizado'", (f"Cancelado: {motivo_cancelamento}", carona_id))
        conexao.commit()
    finally:
        cursor.close()
        
def model_atualizar_status_solicitacao(conexao, id_solicitacao, novo_status):
    cursor = conexao.cursor(cursor_factory=RealDictCursor)
    try:
        # 1. Descobre o status atual antes de mudar e qual é a carona
        cursor.execute("SELECT status, carona_id FROM solicitacoes WHERE id = %s", (id_solicitacao,))
        status_antigo_reg = cursor.fetchone()
        
        if not status_antigo_reg:
            return None
            
        status_antigo = status_antigo_reg["status"]

        # 2. Se não mudou nada de verdade, apenas retorna as infos de notificação
        if status_antigo == novo_status:
            cursor.execute("""
                SELECT u_pass.fcm_token as passageiro_token, c.evento_nome 
                FROM solicitacoes s
                JOIN usuarios u_pass ON s.passageiro_cpf = u_pass.cpf
                JOIN caronas c ON s.carona_id = c.id
                WHERE s.id = %s
            """, (id_solicitacao,))
            return cursor.fetchone()

        # 3. Atualiza APENAS o status da solicitação (Sem tocar na tabela caronas!)
        cursor.execute("UPDATE solicitacoes SET status = %s WHERE id = %s", (novo_status, id_solicitacao))
                
        # 4. Busca os tokens FCM para as notificações push
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