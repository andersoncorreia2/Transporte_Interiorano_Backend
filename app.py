import os
import urllib.parse
import psycopg2 
from psycopg2.extras import RealDictCursor
from psycopg2 import IntegrityError
from flask import Flask, jsonify, request
import firebase_admin
from firebase_admin import credentials, messaging
import json
from datetime import datetime, timedelta
from werkzeug.security import generate_password_hash, check_password_hash
import jwt
from functools import wraps
import random
import smtplib
from email.mime.text import MIMEText

app = Flask(__name__)

# --- CONFIGURAÇÃO SEGURA DO FIREBASE ---
firebase_config_str = os.environ.get("FIREBASE_CONFIG_JSON")

if firebase_config_str:
    try:
        firebase_config = json.loads(firebase_config_str)
        cred = credentials.Certificate(firebase_config)
        firebase_admin.initialize_app(cred)
        print("✅ Firebase inicializado com sucesso!")
    except Exception as e:
        print(f"❌ Erro ao inicializar Firebase: {e}")
else:
    print("⚠️ AVISO: FIREBASE_CONFIG_JSON não encontrada nas variáveis de ambiente!")

JWT_SECRET = os.environ.get("JWT_SECRET", "uma_chave_secreta_super_robusta_e_longa_para_desenvolvimento")

def token_requerido(f):    
    @wraps(f)
    def decorated(*args, **kwargs):
        token = None
        if 'Authorization' in request.headers:
            auth_header = request.headers['Authorization']
            if auth_header.startswith("Bearer "):
                token = auth_header.split(" ")[1]

        if not token:
            return jsonify({"erro": "Token de autenticação ausente!"}), 401

        try:
            dados_token = jwt.decode(token, JWT_SECRET, algorithms=["HS256"])
            request.usuario_logado = dados_token
        except jwt.ExpiredSignatureError:
            return jsonify({"erro": "A sua sessão expirou! Faça login novamente."}), 401
        except jwt.InvalidTokenError:
            return jsonify({"erro": "Token inválido ou corrompido!"}), 401

        return f(*args, **kwargs)
    return decorated

def conectar_banco():
    DATABASE_URL = os.environ.get("DATABASE_URL")
    try:
        conexao = psycopg2.connect(DATABASE_URL)
        return conexao
    except Exception as e:
        print(f"Erro ao conectar no banco: {e}")
        return None

@app.route("/registrar_token", methods=["POST"])
def registrar_token():
    dados = request.get_json()
    conexao = conectar_banco()
    cursor = conexao.cursor()
    cursor.execute("UPDATE usuarios SET fcm_token = %s WHERE email = %s", (dados["token"], dados["email"]))
    conexao.commit()
    cursor.close()
    conexao.close()
    return jsonify({"mensagem": "Token salvo"}), 200

def enviar_notificacao(token, titulo, corpo):
    try:
        message = messaging.Message(
            notification=messaging.Notification(title=titulo, body=corpo),
            token=token,
        )
        messaging.send(message)
    except Exception as e:
        print(f"Erro ao enviar notificação: {e}")

def criar_tabelas():
    conexao = conectar_banco()
    cursor = conexao.cursor()
    try:
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS usuarios (
                cpf TEXT PRIMARY KEY,
                nome TEXT NOT NULL,
                email TEXT UNIQUE NOT NULL,
                telefone TEXT NOT NULL,
                veiculo TEXT,
                placa TEXT,
                senha TEXT NOT NULL,
                vagas TEXT,
                rua TEXT,
                numero TEXT,
                complemento TEXT,
                bairro TEXT,
                cidade TEXT,
                estado TEXT,
                cep TEXT
            )
        """)
        # 🟢 ADIÇÃO CRÍTICA DO CAMPO USUÁRIO SE ELE NÃO EXISTIR
        cursor.execute("ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS usuario TEXT UNIQUE;")
        cursor.execute("ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS fcm_token TEXT;")
        cursor.execute("ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS data_cadastro TEXT DEFAULT '15/06/2026';")
        cursor.execute("ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS corridas_realizadas INTEGER DEFAULT 0;")
        cursor.execute("ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS passageiros_conduzidos INTEGER DEFAULT 0;")
        # 🟢 ADICIONE ESTA LINHA LOGO ABAIXO NO SEU CRiAR_TABELAS:
        cursor.execute("ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS vagas_ofertadas INTEGER DEFAULT 0;")

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS caronas (
                id SERIAL PRIMARY KEY,
                evento_nome TEXT,
                cidade_origem TEXT,
                endereco_origem TEXT,
                cidade_destino TEXT,
                endereco_destino TEXT,
                horario TEXT,
                vagas TEXT,
                motorista TEXT,
                status TEXT DEFAULT 'Aberta'
            )
        """)
        cursor.execute("ALTER TABLE caronas ADD COLUMN IF NOT EXISTS status TEXT DEFAULT 'Aberta';")
        cursor.execute("ALTER TABLE caronas ADD COLUMN IF NOT EXISTS motorista_cpf TEXT;")

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS solicitacoes (
                id SERIAL PRIMARY KEY,
                carona_id INTEGER,
                passageiro TEXT,
                status TEXT
            )
        """)
        cursor.execute("ALTER TABLE solicitacoes ADD COLUMN IF NOT EXISTS passageiro_cpf TEXT;")
        cursor.execute("ALTER TABLE solicitacoes ADD COLUMN IF NOT EXISTS data_criacao TIMESTAMP DEFAULT CURRENT_TIMESTAMP;")

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS codigos_recuperacao (
                email TEXT PRIMARY KEY,
                codigo TEXT NOT NULL,
                expiracao TIMESTAMP NOT NULL
            )
        """)
        conexao.commit()
        print("✅ Tabelas e colunas verificadas com sucesso!")
    except Exception as e:
        print(f"❌ Erro ao criar tabelas: {e}")
        conexao.rollback()
    finally:
        cursor.close()
        conexao.close()

criar_tabelas()

# 🆕 ROTA DE VERIFICAÇÃO DE DISPONIBILIDADE E SUGESTÕES DE USERNAMES LIBERADOS
@app.route("/verificar_usuario/<username>", methods=["GET"])
def verificar_usuario(username):
    user_limpo = username.strip().lower()
    if len(user_limpo) < 3:
        return jsonify({"disponivel": False, "sugestoes": []}), 200
        
    conexao = conectar_banco()
    cursor = conexao.cursor()
    cursor.execute("SELECT usuario FROM usuarios WHERE LOWER(usuario) = %s", (user_limpo,))
    existe = cursor.fetchone()
    
    if not existe:
        cursor.close()
        conexao.close()
        return jsonify({"disponivel": True, "sugestoes": []}), 200
    
    # Se já existir, cria 3 alternativas válidas no banco de dados para sugerir
    sugestoes = []
    while len(sugestoes) < 3:
        numero_aleatorio = random.randint(10, 999)
        prefixos = ["", "id_", "user_"]
        escolha = random.choice(prefixos)
        
        if escolha == "":
            tentativa = f"{user_limpo}{numero_aleatorio}"
        else:
            tentativa = f"{escolha}{user_limpo}"
            
        cursor.execute("SELECT usuario FROM usuarios WHERE LOWER(usuario) = %s", (tentativa,))
        if not cursor.fetchone() and tentativa not in sugestoes:
            sugestoes.append(tentativa)
            
    cursor.close()
    conexao.close()
    return jsonify({"disponivel": False, "sugestoes": sugestoes}), 200

@app.route("/usuarios", methods=["POST"])
def cadastrar_usuario():
    dados = request.get_json()
    conexao = conectar_banco()
    cursor = conexao.cursor()
    try:
        senha_criptografada = generate_password_hash(dados["senha"])
        data_atual = datetime.now()
        data_formatada = data_atual.strftime("%d/%m/%Y")
        
        email_salvar = dados["email"].strip().lower()
        usuario_salvar = dados["usuario"].strip().lower() # 🟢 NOVO CAMPO
        
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
        return jsonify({"mensagem": "Usuário guardado!"}), 201
    except IntegrityError:
        conexao.rollback()
        return jsonify({"erro": "Esse CPF, E-mail ou Nome de Usuário já está cadastrado!"}), 400
    finally:
        cursor.close()
        conexao.close()

# 🔄 ROTA ALTERADA: Atualização agora localiza por CPF e altera e-mail livremente!
@app.route("/usuarios/<cpf_seguro>", methods=["PUT"])
@token_requerido
def atualizar_usuario(cpf_seguro):
    cpf_real = urllib.parse.unquote(cpf_seguro)
    
    if request.usuario_logado["cpf"] != cpf_real:
        return jsonify({"erro": "Ação não autorizada! Você não tem permissão."}), 403

    dados = request.get_json()
    conexao = conectar_banco()
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
        return jsonify({"mensagem": "Dados updated com sucesso!"}), 200
    finally:
        cursor.close()
        conexao.close()

@app.route("/verificar_cpf/<cpf_digitado>", methods=["GET"])
def checar_cpf(cpf_digitado):
    conexao = conectar_banco()
    cursor = conexao.cursor()
    cursor.execute("SELECT cpf FROM usuarios WHERE cpf = %s", (cpf_digitado,))
    usuario_encontrado = cursor.fetchone()
    cursor.close()
    conexao.close()
    return jsonify({"existe": usuario_encontrado is not None}), 200

@app.route("/usuarios_por_email/<email_seguro>", methods=["GET"])
def buscar_por_email(email_seguro):
    email_real = urllib.parse.unquote(email_seguro)
    conexao = conectar_banco()
    cursor = conexao.cursor(cursor_factory=RealDictCursor)
    cursor.execute("SELECT corridas_realizadas, passageiros_conduzidos FROM usuarios WHERE email = %s", (email_real,))
    usuario = cursor.fetchone()
    cursor.close()
    conexao.close()
    return jsonify(usuario if usuario else {"corridas_realizadas": 0, "passageiros_conduzidos": 0}), 200

@app.route("/usuarios_por_nome/<nome_motorista>", methods=["GET"])
def buscar_por_nome(nome_motorista):
    nome_real = urllib.parse.unquote(nome_motorista)
    conexao = conectar_banco()
    cursor = conexao.cursor(cursor_factory=RealDictCursor)
    cursor.execute("""
        SELECT corridas_realizadas, passageiros_conduzidos 
        FROM usuarios 
        WHERE TRIM(LOWER(nome)) = TRIM(LOWER(%s))
    """, (nome_real,))
    usuario = cursor.fetchone()
    cursor.close()
    conexao.close()
    return jsonify(usuario if usuario else {"corridas_realizadas": 0, "passageiros_conduzidos": 0}), 200

@app.route("/usuarios_por_cpf/<cpf>", methods=["GET"])
def buscar_por_cpf(cpf):
    conexao = conectar_banco()
    cursor = conexao.cursor(cursor_factory=RealDictCursor)
    cursor.execute("SELECT corridas_realizadas, passageiros_conduzidos FROM usuarios WHERE cpf = %s", (cpf,))
    usuario = cursor.fetchone()
    cursor.close()
    conexao.close()
    return jsonify(usuario if usuario else {"corridas_realizadas": 0, "passageiros_conduzidos": 0}), 200

@app.route("/usuarios/<email_seguro>", methods=["DELETE"])
@token_requerido
def excluir_conta(email_seguro):
    email_real = urllib.parse.unquote(email_seguro)
    if request.usuario_logado["email"] != email_real:
        return jsonify({"erro": "Ação não autorizada!"}), 403

    conexao = conectar_banco()
    cursor = conexao.cursor(cursor_factory=RealDictCursor)
    cursor.execute("SELECT nome FROM usuarios WHERE email = %s", (email_real,))
    usuario = cursor.fetchone()
    
    if usuario:
        nome_usuario = usuario["nome"]
        cursor.execute("DELETE FROM caronas WHERE motorista = %s", (nome_usuario,))
        cursor.execute("DELETE FROM solicitacoes WHERE passageiro = %s", (nome_usuario,))
        cursor.execute("DELETE FROM usuarios WHERE email = %s", (email_real,))
        conexao.commit()
        cursor.close()
        conexao.close()
        return jsonify({"mensagem": "Conta e dados excluídos definitivamente!"}), 200
    else:
        cursor.close()
        conexao.close()
        return jsonify({"erro": "Usuário não encontrado."}), 404

# 🔄 ROTA MODIFICADA: Login agora valida com Usuário (username) e Senha!
@app.route("/login", methods=["POST"])
def login():
    dados = request.get_json()
    conexao = conectar_banco()
    cursor = conexao.cursor(cursor_factory=RealDictCursor)
    
    cursor.execute("""
        SELECT nome, cpf, email, usuario, telefone, veiculo, placa, vagas, 
               rua, numero, complemento, bairro, cidade, estado, cep, senha, data_cadastro 
        FROM usuarios 
        WHERE LOWER(usuario) = %s
    """, (dados["usuario"].strip().lower(),))

    usuario = cursor.fetchone()
    cursor.close()
    conexao.close()

    is_valido = False
    if usuario:
        if usuario["senha"].startswith(("pbkdf2:", "scrypt:", "bcrypt:")):
            is_valido = check_password_hash(usuario["senha"], dados["senha"])
        else:
            is_valido = (usuario["senha"] == dados["senha"])
            if is_valido:
                try:
                    conn_migrar = conectar_banco()
                    curr_migrar = conn_migrar.cursor()
                    novo_hash_seguro = generate_password_hash(dados["senha"])
                    curr_migrar.execute("UPDATE usuarios SET senha = %s WHERE cpf = %s", (novo_hash_seguro, usuario["cpf"]))
                    conn_migrar.commit()
                    curr_migrar.close()
                    conn_migrar.close()
                except Exception as e:
                    print(f"⚠️ Erro ao atualizar hash: {e}")

    if is_valido:
        tempo_expiracao = datetime.utcnow() + timedelta(hours=24)
        token = jwt.encode(
            {"email": usuario["email"], "cpf": usuario["cpf"], "usuario": usuario["usuario"], "exp": tempo_expiracao},
            JWT_SECRET,
            algorithm="HS256"
        )
        return jsonify({
            "token": token,
            "usuario": {
                "nome": usuario["nome"], "cpf": usuario["cpf"], "email": usuario["email"], "usuario": usuario["usuario"],
                "telefone": usuario["telefone"], "veiculo": usuario.get("veiculo", ""),
                "placa": usuario.get("placa", ""), "vagas": usuario.get("vagas", "0"),
                "rua": usuario.get("rua", ""), "numero": usuario.get("numero", ""),
                "complemento": usuario.get("complemento", ""), "bairro": usuario.get("bairro", ""),
                "cidade": usuario.get("cidade", ""), "estado": usuario.get("estado", ""), "cep": usuario.get("cep", ""),
                "data_cadastro": usuario.get("data_cadastro", "15/06/2026")
            }
        }), 200
    else:
        return jsonify({"erro": "Nome de usuário ou senha incorretos"}), 401

# 🟢 SUBSTITUA O BLOCO CORTADO POR ESTE COMPLETO E CORRIGIDO:
@app.route("/solicitar_codigo", methods=["POST"])
def solicitar_codigo():
    dados = request.get_json()
    
    # Remove espaços e joga para minúsculo no próprio Python
    email_digitado = dados.get("email", "").strip().lower()
    cpf_digitado = dados.get("cpf", "").strip()
    
    # Garante que o CPF que vai buscar tenha apenas números
    cpf_limpo = ''.join(filter(str.isdigit(), cpf_digitado))

    conexao = conectar_banco()
    cursor = conexao.cursor(cursor_factory=RealDictCursor)
    
    print(f"🔎 BUSCANDO RECUPERAÇÃO -> Email: '{email_digitado}' | CPF: '{cpf_limpo}'")
    
    # Query corrigida: fecha as aspas, os parâmetros e os parênteses perfeitamente!
    cursor.execute("""
        SELECT email, cpf FROM usuarios 
        WHERE LOWER(email) = %s 
        AND cpf = %s
    """, (email_digitado, cpf_limpo))
    
    usuario = cursor.fetchone()

    print(f"📊 RESULTADO DO BANCO -> Encontrou: {usuario}")

    if not usuario:
        cursor.close()
        conexao.close()
        return jsonify({"erro": "E-mail ou CPF não encontrados."}), 404

    codigo = str(random.randint(100000, 999999))
    expiracao = datetime.now() + timedelta(minutes=10)

    cursor.execute("""
        INSERT INTO codigos_recuperacao (email, codigo, expiracao)
        VALUES (%s, %s, %s)
        ON CONFLICT (email) DO UPDATE SET codigo = EXCLUDED.codigo, expiracao = EXCLUDED.expiracao
    """, (usuario["email"], codigo, expiracao))
    
    conexao.commit()
    cursor.close()
    conexao.close()

    smtp_user = os.environ.get("SMTP_USER")
    smtp_pass = os.environ.get("SMTP_PASS")
    
    if not smtp_user or not smtp_pass:
        return jsonify({"erro": "Servidor de e-mail não configurado nas variáveis de ambiente do Render."}), 500

    try:
        msg = MIMEText(f"Seu código de verificação do Transporte Interiorano é: {codigo}\nValidade: 10 minutos.")
        msg['Subject'] = 'Código de Recuperação de Senha'
        msg['From'] = smtp_user
        msg['To'] = usuario["email"]
        
        with smtplib.SMTP_SSL('smtp.gmail.com', 465, timeout=10) as server:
            server.login(smtp_user, smtp_pass)
            server.send_message(msg)
            
        return jsonify({"mensagem": "Código enviado para o e-mail cadastrado!"}), 200

    except Exception as e:
        print(f"❌ ERRO REAL CRÍTICO SMTP NO RENDER: {e}")
        return jsonify({"erro": f"O servidor falhou ao despachar o e-mail: {str(e)}"}), 500

@app.route("/validar_e_redefinir_senha", methods=["POST"])
def validar_e_redefinir_senha():
    dados = request.get_json()
    email = dados.get("email")
    codigo = dados.get("codigo")
    nova_senha = dados.get("senha")

    conexao = conectar_banco()
    cursor = conexao.cursor(cursor_factory=RealDictCursor)
    cursor.execute("SELECT codigo, expiracao FROM codigos_recuperacao WHERE email = %s", (email,))
    registro = cursor.fetchone()

    if not registro or registro["codigo"] != str(codigo).strip() or datetime.now() > registro["expiracao"]:
        cursor.close()
        conexao.close()
        return jsonify({"erro": "Código de verificação incorreto ou expirado!"}), 400

    nova_senha_hash = generate_password_hash(nova_senha)
    cursor.execute("UPDATE usuarios SET senha = %s WHERE email = %s", (nova_senha_hash, email))
    cursor.execute("DELETE FROM codigos_recuperacao WHERE email = %s", (email,))
    conexao.commit()
    cursor.close()
    conexao.close()
    return jsonify({"mensagem": "Senha alterada com sucesso!"}), 200

@app.route("/caronas", methods=["GET"])
def listar_caronas():
    conexao = conectar_banco()
    cursor = conexao.cursor(cursor_factory=RealDictCursor)
    cursor.execute("SELECT * FROM caronas WHERE status = 'Aberta'") 
    caronas_do_cofre = cursor.fetchall()
    cursor.close()
    conexao.close()
    return jsonify(caronas_do_cofre)

@app.route("/caronas", methods=["POST"])
def criar_carona():
    dados = request.get_json()
    conexao = conectar_banco()
    cursor = conexao.cursor()
    cursor.execute("""
        INSERT INTO caronas (evento_nome, cidade_origem, endereco_origem, cidade_destino, endereco_destino, horario, vagas, motorista, motorista_cpf)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
    """, (dados["evento_nome"], dados["cidade_origem"], dados["endereco_origem"], 
          dados["cidade_destino"], dados["endereco_destino"], dados["horario"], 
          dados["vagas"], dados["motorista"], dados["motorista_cpf"]))
    conexao.commit()
    cursor.close()
    conexao.close()
    return jsonify({"mensagem": "Evento criado!"}), 201

@app.route("/caronas/<int:id_carona>", methods=["DELETE"])
def deletar_carona(id_carona):
    conexao = conectar_banco()
    cursor = conexao.cursor()
    try:
        # 🟢 CORRIGIDO: Primeiro deletamos as solicitações vinculadas para liberar a chave estrangeira
        cursor.execute("DELETE FROM solicitacoes WHERE carona_id = %s", (id_carona,))
        
        # 🟢 Agora sim, deletamos o evento com segurança sem violar o constraint do Postgres
        cursor.execute("DELETE FROM caronas WHERE id = %s", (id_carona,))
        
        conexao.commit()
        return jsonify({"mensagem": "Evento e solicitações excluídos com sucesso!"}), 200
    except Exception as e:
        conexao.rollback()
        print(f"❌ Erro ao deletar carona: {e}")
        return jsonify({"erro": str(e)}), 500
    finally:
        cursor.close()
        conexao.close()

@app.route("/solicitacoes", methods=["GET"])
def listar_solicitacoes():
    conexao = conectar_banco()
    cursor = conexao.cursor(cursor_factory=RealDictCursor)
    cursor.execute("DELETE FROM solicitacoes WHERE status = 'Expirado'")
    conexao.commit()
    
    # 🟢 CORRIGIDO: Seleciona todos os registros para manter o histórico visível no DBeaver e no App
    cursor.execute("SELECT * FROM solicitacoes")
    solicitacoes_do_cofre = cursor.fetchall()
    lista_solicitacoes = []
    agora = datetime.now()

    for sol in solicitacoes_do_cofre:
        status = sol["status"]
        if status == "Pendente" and sol["data_criacao"]:
            if (agora - sol["data_criacao"]) > timedelta(minutes=15):
                status = "Expirado"
                cursor.execute("UPDATE solicitacoes SET status = %s WHERE id = %s", (status, sol["id"]))
                conexao.commit()
                
        # 🟢 CORRIGIDO: Garante o envio do objeto completo preenchido para o radar do Kotlin
        lista_solicitacoes.append({
            "id": sol["id"], 
            "carona_id": sol["carona_id"], 
            "passageiro": sol["passageiro"], 
            "status": status,
            "passageiro_cpf": sol.get("passageiro_cpf", "")
        })
    cursor.close()
    conexao.close()
    return jsonify(lista_solicitacoes), 200

@app.route("/solicitacoes", methods=["POST"])
def pedir_carona():
    dados = request.get_json()
    carona_id = int(dados["carona_id"])
    cpf_passageiro = dados.get("passageiro_cpf")
    
    conexao = conectar_banco()
    cursor = conexao.cursor(cursor_factory=RealDictCursor)
    
    # Busca para validar a carona e capturar o motorista correspondente
    cursor.execute("SELECT vagas, motorista_cpf FROM caronas WHERE id = %s", (carona_id,))
    carona = cursor.fetchone()
    
    if carona:
        # 🟢 CORRIGIDO: O valor absoluto das vagas na tabela caronas FICA FIXO (ex: 4).
        # Apenas gravamos a solicitação como Pendente. O Kotlin deduz dinamicamente.
        cursor.execute("""
            INSERT INTO solicitacoes (carona_id, passageiro, passageiro_cpf, status, data_criacao) 
            VALUES (%s, %s, %s, %s, %s)
        """, (carona_id, dados["passageiro"], cpf_passageiro, "Pendente", datetime.now()))
        
        # Envia a notificação FCM para o motorista dono da carona
        cursor.execute("SELECT fcm_token FROM usuarios WHERE cpf = %s", (carona["motorista_cpf"],))
        motorista = cursor.fetchone()
        if motorista and motorista.get("fcm_token"):
            enviar_notificacao(motorista["fcm_token"], "Nova Solicitação!", f"{dados['passageiro']} quer uma vaga.")
            
        conexao.commit()
        cursor.close()
        conexao.close()
        return jsonify({"mensagem": "Pedido registrado com sucesso!"}), 201

    cursor.close()
    conexao.close()
    return jsonify({"erro": "Carona inexistente."}), 400

@app.route("/solicitacoes/<int:id_solicitacao>", methods=["PUT"])
def responder_solicitacao(id_solicitacao):
    dados = request.get_json()
    status_recebido = dados.get("status")
    
    conexao = conectar_banco()
    cursor = conexao.cursor()
    try:
        # 🟢 CORRIGIDO: Força o update de status com tratamento de exceção estruturado
        cursor.execute("UPDATE solicitacoes SET status = %s WHERE id = %s", (status_recebido, id_solicitacao))
        conexao.commit()
        return jsonify({"mensagem": "Status atualizado com sucesso!"}), 200
    except Exception as e:
        conexao.rollback()
        print(f"❌ Erro ao atualizar status: {e}")
        return jsonify({"erro": str(e)}), 500
    finally:
        cursor.close()
        conexao.close()

@app.route("/solicitacoes/<int:id_solicitacao>", methods=["DELETE"])
def cancelar_solicitacao(id_solicitacao):
    conexao = conectar_banco()
    cursor = conexao.cursor(cursor_factory=RealDictCursor)
    
    cursor.execute("SELECT id FROM solicitacoes WHERE id = %s", (id_solicitacao,))
    pedido = cursor.fetchone()
    
    if pedido:
        # 🟢 CORRIGIDO: Remove o pedido sem tentar fazer UPDATE na tabela caronas
        cursor.execute("DELETE FROM solicitacoes WHERE id = %s", (id_solicitacao,))
        conexao.commit()
        cursor.close()
        conexao.close()
        return jsonify({"mensagem": "Pedido cancelado com sucesso!"}), 200
        
    cursor.close()
    conexao.close()
    return jsonify({"erro": "Solicitação não encontrada."}), 404

@app.route("/finalizar_solicitacao", methods=["POST"])
def finalizar_solicitacao():
    dados = request.get_json()
    conexao = conectar_banco()
    cursor = conexao.cursor(cursor_factory=RealDictCursor)
    try:
        # 1. Atualiza a solicitação específica do passageiro para Finalizado
        cursor.execute("UPDATE solicitacoes SET status = 'Finalizado' WHERE id = %s", (dados["solicitacao_id"],))
        
        # 2. Busca os dados de amarrações e o número de vagas totais originais do evento
        cursor.execute("""
            SELECT s.passageiro_cpf, c.motorista_cpf, c.vagas, c.id as carona_real_id 
            FROM solicitacoes s 
            JOIN caronas c ON s.carona_id = c.id 
            WHERE s.id = %s
        """, (dados["solicitacao_id"],))
        info = cursor.fetchone()
        
        # 3. Passageiro recebe +1 corrida realizada no ato da sua finalização individual
        cursor.execute("UPDATE usuarios SET corridas_realizadas = COALESCE(corridas_realizadas, 0) + 1 WHERE cpf = %s", (info['passageiro_cpf'],))
        
        # 4. Motorista recebe +1 passageiro conduzido por esta finalização
        cursor.execute("UPDATE usuarios SET passageiros_conduzidos = COALESCE(passageiros_conduzidos, 0) + 1 WHERE cpf = %s", (info['motorista_cpf'],))
        
        # 5. Verifica se este era o último passageiro ativo (Pendente ou Aceito) do evento
        cursor.execute("SELECT count(*) as count FROM solicitacoes WHERE carona_id = %s AND status != 'Finalizado'", (info["carona_real_id"],))
        restantes = cursor.fetchone()['count']
        
        # Se não houver mais nenhum passageiro pendente/aceito, fecha o evento e computa os pontos do motorista
        if restantes == 0:
            # Motorista ganha +1 corrida realizada
            cursor.execute("UPDATE usuarios SET corridas_realizadas = COALESCE(corridas_realizadas, 0) + 1 WHERE cpf = %s", (info['motorista_cpf'],))
            # Evento passa a ser Finalizado
            cursor.execute("UPDATE caronas SET status = 'Finalizado' WHERE id = %s", (info["carona_real_id"],))
            
            # 🟢 REGRA MÁGICA: Soma as vagas disponibilizadas originalmente no evento (ex: 4) na coluna vagas_ofertadas do motorista, independente de ocupação!
            vagas_do_evento = int(info['vagas']) if info['vagas'] else 4
            cursor.execute("UPDATE usuarios SET vagas_ofertadas = COALESCE(vagas_ofertadas, 0) + %s WHERE cpf = %s", (vagas_do_evento, info['motorista_cpf']))
            
        conexao.commit()
        return jsonify({"mensagem": "Viagem finalizada com sucesso!"}), 200
    except Exception as e:
        conexao.rollback()
        return jsonify({"erro": str(e)}), 500
    finally:
        cursor.close()
        conexao.close()

@app.route("/historico_cpf/<cpf>", methods=["GET"])
def listar_historico_passageiro_por_cpf(cpf):
    conexao = conectar_banco()
    cursor = conexao.cursor(cursor_factory=RealDictCursor)
    cursor.execute("SELECT s.*, c.evento_nome, c.horario, s.passageiro_cpf FROM solicitacoes s JOIN caronas c ON s.carona_id = c.id WHERE s.passageiro_cpf = %s AND s.status = 'Finalizado'", (urllib.parse.unquote(cpf),))
    historico = cursor.fetchall()
    cursor.close()
    conexao.close()
    return jsonify(historico), 200

@app.route("/historico_motorista_cpf/<cpf>", methods=["GET"])
def listar_historico_motorista_por_cpf(cpf):
    conexao = conectar_banco()
    cursor = conexao.cursor(cursor_factory=RealDictCursor)
    cursor.execute("SELECT s.*, c.evento_nome, c.horario, s.passageiro_cpf FROM solicitacoes s JOIN caronas c ON s.carona_id = c.id WHERE c.motorista_cpf = %s AND s.status = 'Finalizado'", (urllib.parse.unquote(cpf),))
    historico = cursor.fetchall()
    cursor.close()
    conexao.close()
    return jsonify(historico), 200

if __name__ == "__main__":
    porta = int(os.environ.get("PORT", 5000))
    app.run(debug=False, host="0.0.0.0", port=porta)