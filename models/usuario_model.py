import urllib.parse
import random
from psycopg2.extras import RealDictCursor
from datetime import datetime, timezone

def model_atualizar_token_fcm(conexao, token, email):
    cursor = conexao.cursor(cursor_factory=RealDictCursor)
    try:
        cursor.execute("UPDATE usuarios SET fcm_token = %s WHERE email = %s", (token, email))
        conexao.commit()
    finally:
        cursor.close()

def model_buscar_usuario_por_username(conexao, username):
    cursor = conexao.cursor(cursor_factory=RealDictCursor)
    try:
        cursor.execute("SELECT usuario FROM usuarios WHERE LOWER(usuario) = %s", (username,))
        return cursor.fetchone()
    finally:
        cursor.close()

def model_verificar_sugestao_existe(conexao, sugestao):
    cursor = conexao.cursor(cursor_factory=RealDictCursor)
    try:
        cursor.execute("SELECT 1 FROM usuarios WHERE LOWER(usuario) = %s", (sugestao,))
        return cursor.fetchone()
    finally:
        cursor.close()

def model_atualizar_modalidade(conexao, modalidade, cpf_usuario):
    cursor = conexao.cursor()
    try:
        cursor.execute("UPDATE usuarios SET modalidade_ativa = %s WHERE cpf = %s", (modalidade, cpf_usuario))
        conexao.commit()
    finally:
        cursor.close()

def model_inserir_usuario(conexao, dados, senha_criptografada):
    cursor = conexao.cursor(cursor_factory=RealDictCursor)   
    data_atual = datetime.now(timezone.utc)
    data_formatada = data_atual.strftime("%d/%m/%Y")        
    email_salvar = dados["email"].strip().lower()
    usuario_salvar = dados["usuario"].strip().lower()
    
    try:
        cursor.execute("""
            INSERT INTO usuarios (nome, cpf, email, telefone, veiculo, placa, senha, vagas, rua, numero, complemento, bairro, cidade, estado, cep, data_cadastro, usuario)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """, (
            dados["nome"], dados["cpf"], email_salvar, dados["telefone"],
            dados.get("veiculo", ""), dados.get("placa", ""), senha_criptografada, dados.get("vagas", "0"),
            dados.get("rua", ""), dados.get("numero", ""), dados.get("complemento", ""),
            dados.get("bairro", ""), dados.get("cidade", ""), dados.get("estado", ""), dados.get("cep", ""),
            data_formatada, usuario_salvar
        ))
        conexao.commit()
    finally:
        cursor.close()

def model_atualizar_usuario(conexao, dados, cpf_real):
    cursor = conexao.cursor()
    try:
        cursor.execute("""
            UPDATE usuarios 
            SET nome=%s, telefone=%s, email=%s, veiculo=%s, placa=%s, vagas=%s,
                rua=%s, numero=%s, complemento=%s, bairro=%s, cidade=%s, estado=%s, cep=%s
            WHERE cpf=%s
        """, (
            dados["nome"], dados["telefone"], dados["email"].strip().lower(), dados.get("veiculo", ""), 
            dados.get("placa", ""), dados.get("vagas", "0"), dados.get("rua", ""), dados.get("numero", ""), 
            dados.get("complemento", ""), dados.get("bairro", ""), dados.get("cidade", ""), 
            dados.get("estado", ""), dados.get("cep", ""), cpf_real
        ))
        conexao.commit()
    finally:
        cursor.close()

def model_checar_cpf_existe(conexao, cpf_digitado):
    cursor = conexao.cursor()
    try:
        cursor.execute("SELECT cpf FROM usuarios WHERE cpf = %s", (cpf_digitado,))
        return cursor.fetchone() is not None
    finally:
        cursor.close()

def model_buscar_usuario_por_email(conexao, email_real):
    cursor = conexao.cursor(cursor_factory=RealDictCursor)
    try:
        cursor.execute("SELECT corridas_realizadas, passageiros_conduzidos FROM usuarios WHERE email = %s", (email_real,))
        return cursor.fetchone()
    finally:
        cursor.close()

def model_buscar_usuario_por_nome(conexao, nome_real):
    cursor = conexao.cursor(cursor_factory=RealDictCursor)
    try:
        cursor.execute("SELECT corridas_realizadas, passageiros_conduzidos FROM usuarios WHERE TRIM(LOWER(nome)) = TRIM(LOWER(%s))", (nome_real,))
        return cursor.fetchone()
    finally:
        cursor.close()

def model_buscar_usuario_por_cpf(conexao, cpf):
    cursor = conexao.cursor(cursor_factory=RealDictCursor)
    try:
        cursor.execute("SELECT corridas_realizadas, passageiros_conduzidos FROM usuarios WHERE cpf = %s", (cpf,))
        return cursor.fetchone()
    finally:
        cursor.close()

def model_excluir_conta_usuario(conexao, email_real):
    cursor = conexao.cursor(cursor_factory=RealDictCursor)
    try:
        cursor.execute("SELECT nome FROM usuarios WHERE email = %s", (email_real,))
        usuario = cursor.fetchone()
        
        if usuario:
            nome_usuario = usuario["nome"]
            cursor.execute("DELETE FROM caronas WHERE motorista = %s", (nome_usuario,))
            cursor.execute("DELETE FROM solicitacoes WHERE passageiro = %s", (nome_usuario,))
            cursor.execute("DELETE FROM usuarios WHERE email = %s", (email_real,))
            conexao.commit()
            return True
        return False
    finally:
        cursor.close()

def model_buscar_dados_login(conexao, username):
    cursor = conexao.cursor(cursor_factory=RealDictCursor)
    try:
        cursor.execute("""
            SELECT nome, cpf, email, usuario, telefone, veiculo, placa, vagas, 
                   rua, numero, complemento, bairro, cidade, estado, cep, senha, data_cadastro 
            FROM usuarios 
            WHERE LOWER(usuario) = %s
        """, (username,))
        return cursor.fetchone()
    finally:
        cursor.close()

def model_atualizar_hash_senha(conexao, novo_hash, cpf):
    cursor = conexao.cursor()
    try:
        cursor.execute("UPDATE usuarios SET senha = %s WHERE cpf = %s", (novo_hash, cpf))
        conexao.commit()
    except Exception as e:
        print(f"⚠️ Erro ao atualizar hash no model: {e}")

def model_buscar_recuperacao(conexao, email_digitado, cpf_limpo):
    cursor = conexao.cursor(cursor_factory=RealDictCursor)
    try:
        cursor.execute("""
            SELECT email, cpf FROM usuarios 
            WHERE LOWER(TRIM(email)) = %s 
            AND regexp_replace(cpf, '\\D', '', 'g') = %s
        """, (email_digitado, cpf_limpo))
        return cursor.fetchone()
    finally:
        cursor.close()

def model_salvar_codigo_recuperacao(conexao, email, codigo, expiracao):
    cursor = conexao.cursor()
    try:
        cursor.execute("""
            INSERT INTO codigos_recuperacao (email, codigo, expiracao)
            VALUES (%s, %s, %s)
            ON CONFLICT (email) DO UPDATE SET codigo = EXCLUDED.codigo, expiracao = EXCLUDED.expiracao
        """, (email, codigo, expiracao))
        conexao.commit()
    finally:
        cursor.close()

def model_buscar_codigo_recuperacao(conexao, email):
    cursor = conexao.cursor(cursor_factory=RealDictCursor)
    try:
        cursor.execute("SELECT codigo, expiracao FROM codigos_recuperacao WHERE email = %s", (email,))
        return cursor.fetchone()
    finally:
        cursor.close()

def model_redefinir_senha_final(conexao, nova_senha_hash, email):
    cursor = conexao.cursor()
    try:
        cursor.execute("UPDATE usuarios SET senha = %s WHERE email = %s", (nova_senha_hash, email))
        cursor.execute("DELETE FROM codigos_recuperacao WHERE email = %s", (email,))
        conexao.commit()
    finally:
        cursor.close()