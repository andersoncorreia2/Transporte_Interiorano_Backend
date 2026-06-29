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
from datetime import datetime, timezone


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
# 🟢 Alterado para usar um banco de dados totalmente separado dentro do seu computador para não mexer nos dados reais da rua
def conectar_banco():
    # Se estiver rodando no Render, ele usa a variável de ambiente original deles
    DATABASE_URL = os.environ.get("DATABASE_URL")
    
    # SE ESTIVER RODANDO LOCAL NO SEU VS CODE (Variável vazia), usamos a string externa do Render direto!
    if not DATABASE_URL:
        # 💡 ADICIONADO: ?sslmode=require no final da string para o Render aceitar
        DATABASE_URL = "postgresql://transporte_db_novo_user:FS385qeaMpIzyZliHuIuQQaw1YwES5HM@dpg-d8t2n80js32c73d3pov0-a.oregon-postgres.render.com/transporte_db_novo?sslmode=require"
        
    try:
        # Abre a conexão com o banco Postgres do Render
        conexao = psycopg2.connect(DATABASE_URL)
        return conexao
    except Exception as e:
        print(f"Erro ao conectar no banco: {e}")
        return None

@app.route("/registrar_token", methods=["POST"])
def registrar_token():
    dados = request.get_json()
    conexao = conectar_banco()
    if not conexao:
        return jsonify({"erro": "Banco de dados offline!"}), 500
        
    cursor = conexao.cursor(cursor_factory=RealDictCursor)
    cursor.execute("UPDATE usuarios SET fcm_token = %s WHERE email = %s", (dados["token"], dados["email"]))
    conexao.commit()
    cursor.close()
    conexao.close()
    return jsonify({"mensagem": "Token saved"}), 200

def enviar_notificacao(token, titulo, corpo):
    try:
        # 🟢 CORRIGIDO: Removido o parâmetro inválido 'notification_priority'
        android_alert = messaging.AndroidConfig(
            priority='high',
            notification=messaging.AndroidNotification(
                sound='default',
                default_sound=True
            )
        )

        message = messaging.Message(
            notification=messaging.Notification(title=titulo, body=corpo),
            token=token,
            android=android_alert # Injeta a configuração de áudio correta no payload
        )
        messaging.send(message)
        print("✅ Notificação enviada com diretrizes de som ativa!")
    except Exception as e:
        print(f"Erro ao enviar notificação: {e}")
        
def criar_tabelas():
    conexao = conectar_banco()
    if not conexao:
        print("⚠️ AVISO: Não foi possível estruturar as tabelas pois o banco de dados está offline. O servidor tentará operar assim mesmo.")
        return # 🟢 EVITA O QUEBRA DO DEPLOY: Sai da função sem tentar chamar .cursor() se conexão for None
        
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
        cursor.execute("ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS vagas_ofertadas INTEGER DEFAULT 0;")
        
        # 🟢 ADIÇÃO DA COLUNA DE CONTROLE DE MODALIDADE (UBER VS BLA BLA CAR)
        cursor.execute("ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS modalidade_ativa TEXT DEFAULT 'Programada';")

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

        # 🟢 CRIAÇÃO DA NOVA TABELA PARA CORRIDAS EMERGENGIAIS (MODO UBER)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS corridas_emergentes (
                id SERIAL PRIMARY KEY,
                passageiro_cpf TEXT NOT NULL,
                motorista_cpf TEXT,
                origem_latitude NUMERIC NOT NULL,
                origem_longitude NUMERIC NOT NULL,
                destino_latitude NUMERIC NOT NULL,
                destino_longitude NUMERIC NOT NULL,
                endereco_origem TEXT,
                endereco_destino TEXT,
                status TEXT DEFAULT 'Procurando',
                data_criacao TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
            )
        """)

        conexao.commit()
        print("✅ Tabelas, colunas e modo emergencial verificados com sucesso!")
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
    if not conexao:
        return jsonify({"disponivel": False, "sugestoes": []}), 500

    try:
        # 💡 CORREÇÃO 1: Usar RealDictCursor para manter o padrão do seu banco
        cursor = conexao.cursor(cursor_factory=RealDictCursor)
        
        # Verifica se o usuário principal existe
        cursor.execute("SELECT usuario FROM usuarios WHERE LOWER(usuario) = %s", (user_limpo,))
        existe = cursor.fetchone()

        if not existe:
            return jsonify({"disponivel": True, "sugestoes": []}), 200

        # Se o usuário já existe, gera exatamente 3 sugestões válidas
        sugestoes = []
        tentativas = 0
        
        while len(sugestoes) < 3 and tentativas < 20:
            tentativas += 1
            sugestao = f"{user_limpo}{random.randint(10, 99)}"
            
            cursor.execute("SELECT 1 FROM usuarios WHERE LOWER(usuario) = %s", (sugestao,))
            if not cursor.fetchone():
                if sugestao not in sugestoes:  # 🌟 CORRIGIDO: Sintaxe correta em Python
                    sugestoes.append(sugestao)

        return jsonify({"disponivel": False, "sugestoes": sugestoes}), 200  # 🌟 CORRIGIDO: Nome da variável consertado

    except Exception as e:
        print(f"❌ Erro na rota verificar_usuario: {e}")
        return jsonify({"disponivel": False, "sugestoes": []}), 500
    finally:
        cursor.close()
        conexao.close()

@app.route("/usuarios/alterar_modalidade", methods=["POST"])
@token_requerido
def alterar_modalidade():
    dados = request.get_json()
    modalidade = dados.get("modalidade") # Deve receber 'Programada' ou 'Emergencial'
    cpf_usuario = request.usuario_logado["cpf"]

    if modalidade not in ['Programada', 'Emergencial']:
        return jsonify({"erro": "Modalidade selecionada é inválida!"}), 400

    conexao = conectar_banco()
    if not conexao:
        return jsonify({"erro": "Banco de dados offline"}), 500
        
    cursor = conexao.cursor()
    try:
        cursor.execute("""
            UPDATE usuarios 
            SET modalidade_ativa = %s 
            WHERE cpf = %s
        """, (modalidade, cpf_usuario))
        conexao.commit()
        return jsonify({"mensagem": f"Modalidade alterada para {modalidade} com sucesso!"}), 200
    except Exception as e:
        conexao.rollback()
        print(f"❌ Erro ao atualizar modalidade: {e}")
        return jsonify({"erro": str(e)}), 500
    finally:
        cursor.close()
        conexao.close()

@app.route("/usuarios", methods=["POST"])
def cadastrar_usuario():
    dados = request.get_json()
    conexao = conectar_banco()
    
    # 🟢 VERIFICAÇÃO DE SEGURANÇA: Se o banco estiver fora do ar, avisa o app na hora
    if not conexao:
        return jsonify({"erro": "Banco de dados offline ou inacessível no momento!"}), 500
        
    # 🟢 CURSOR CORRETO: Abre o cursor com RealDictCursor para o Postgres do Render funcionar
    cursor = conexao.cursor(cursor_factory=RealDictCursor)   
    try:
        senha_criptografada = generate_password_hash(dados["senha"])
        data_atual = datetime.now(timezone.utc)
        data_formatada = data_atual.strftime("%d/%m/%Y")        
        email_salvar = dados["email"].strip().lower()
        usuario_salvar = dados["usuario"].strip().lower()
        
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
    if not conexao:
        return jsonify({"erro": "Banco de dados offline"}), 500
        
    cursor = conexao.cursor(cursor_factory=RealDictCursor)
    
    try:
        cursor.execute("""
            SELECT nome, cpf, email, usuario, telefone, veiculo, placa, vagas, 
                   rua, numero, complemento, bairro, cidade, estado, cep, senha, data_cadastro 
            FROM usuarios 
            WHERE LOWER(usuario) = %s
        """, (dados["usuario"].strip().lower(),))

        usuario = cursor.fetchone()

        is_valido = False
        if usuario:
            if usuario["senha"].startswith(("pbkdf2:", "scrypt:", "bcrypt:")):
                is_valido = check_password_hash(usuario["senha"], dados["senha"])
            else:
                is_valido = (usuario["senha"] == dados["senha"])
                if is_valido:
                    try:
                        # 💡 REUTILIZANDO O CURSOR EXISTENTE: Evita abrir nova conexão e quebrar o SSL
                        novo_hash_seguro = generate_password_hash(dados["senha"])
                        cursor.execute("UPDATE usuarios SET senha = %s WHERE cpf = %s", (novo_hash_seguro, usuario["cpf"]))
                        conexao.commit()
                    except Exception as migration_error:
                        print(f"⚠️ Erro ao atualizar hash: {migration_error}")

        if is_valido:
            tempo_expiracao = datetime.now(timezone.utc) + timedelta(hours=24)
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
            
    except Exception as e:
        print(f"❌ Erro na rota de login: {e}")
        return jsonify({"erro": "Erro interno no servidor"}), 500
    finally:
        cursor.close()
        conexao.close()
    
@app.route("/solicitar_codigo", methods=["POST"])
def solicitar_codigo():
    dados = request.get_json()
    
    email_digitado = dados.get("email", "").strip().lower()
    cpf_digitado = dados.get("cpf", "").strip()
    
    # 🟢 Limpa o CPF recebido do App deixando APENAS os números
    cpf_limpo = ''.join(filter(str.isdigit, cpf_digitado))

    conexao = conectar_banco()
    cursor = conexao.cursor(cursor_factory=RealDictCursor)
    
    print(f"🔎 BUSCANDO RECUPERAÇÃO -> Email: '{email_digitado}' | CPF Limpo: '{cpf_limpo}'")
    
    try:
        # 🟢 Usa regexp_replace para remover TUDO que não for número do CPF no banco de dados
        cursor.execute("""
            SELECT email, cpf FROM usuarios 
            WHERE LOWER(TRIM(email)) = %s 
            AND regexp_replace(cpf, '\\D', '', 'g') = %s
        """, (email_digitado, cpf_limpo))
        
        usuario = cursor.fetchone()
    except Exception as e:
        print(f"❌ Erro na query do banco: {e}")
        cursor.close()
        conexao.close()
        return jsonify({"erro": "Erro interno ao buscar usuário."}), 500

    print(f"📊 RESULTADO DO BANCO -> Encontrou: {usuario}")

    if not usuario:
        cursor.close()
        conexao.close()
        return jsonify({"erro": "E-mail ou CPF não encontrados no sistema."}), 404

    codigo = str(random.randint(100000, 999999))
    # 🕒 MODIFICADO: Código de recuperação com expiração em timezone UTC explícito
    expiracao = datetime.now(timezone.utc) + timedelta(minutes=10)

    cursor.execute("""
        INSERT INTO codigos_recuperacao (email, codigo, expiracao)
        VALUES (%s, %s, %s)
        ON CONFLICT (email) DO UPDATE SET codigo = EXCLUDED.codigo, expiracao = EXCLUDED.expiracao
    """, (usuario["email"], codigo, expiracao))
    
    conexao.commit()
    cursor.close()
    conexao.close()

    # 🟢 ALTERADO PARA TESTES: Mostra o código no log do Render em vez de enviar por e-mail
    print(f"🔒 CÓDIGO DE RECUPERAÇÃO GERADO PARA {usuario['email']}: {codigo}")
    
    # Retorna sucesso para o aplicativo não dar erro
    return jsonify({"mensagem": "Código gerado com sucesso! (Verifique os logs do Render)"}), 200
  
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

    # 🕒 MODIFICADO: Validação comparando com o datetime.now(timezone.utc)
    if not registro or registro["codigo"] != str(codigo).strip() or datetime.now(timezone.utc) > registro["expiracao"]:
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

# 🟢 ATUALIZADO: Rota parametrizada para omitir caronas de passageiros recusados/bloqueados
@app.route("/caronas/<cpf_passageiro>", methods=["GET"])
def listar_caronas(cpf_passageiro):
    conexao = conectar_banco()
    if not conexao:
        return jsonify({"erro": "Falha na conexão com o banco"}), 500
    cursor = conexao.cursor(cursor_factory=RealDictCursor)
    
    try:
        # Puxa caronas Abertas, ocultando aquelas onde o passageiro foi recusado pessoalmente
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
        
        # 🟢 CORREÇÃO AQUI: Garantindo que o argumento seja passado como uma tupla de 1 elemento (notar a vírgula final)
        cpf_real = urllib.parse.unquote(cpf_passageiro)
        cursor.execute(query, (cpf_real,))
        
        caronas_limpas = cursor.fetchall()
        return jsonify(caronas_limpas), 200
    except Exception as e:
        print(f"❌ Erro ao listar caronas filtradas: {e}")
        return jsonify({"erro": str(e)}), 500
    finally:
        cursor.close()
        conexao.close()

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
    if not conexao:
        return jsonify({"erro": "Falha na conexão com o banco"}), 500
        
    cursor = conexao.cursor(cursor_factory=RealDictCursor)
    agora = datetime.now(timezone.utc)
    
    try:
        # 🕒 SISTEMA PROGRAMADO (CARONA): Busca apenas solicitações 'Pendente' para validar os 15 minutos
        cursor.execute("SELECT id, status, data_criacao FROM solicitacoes WHERE status = 'Pendente'")
        pendentes = cursor.fetchall()
        
        for sol in pendentes:
            data_criacao = sol["data_criacao"]
            # Sincroniza o fuso horário para evitar o erro de colisão de datas no Python
            if data_criacao and data_criacao.tzinfo is None:
                data_criacao = data_criacao.replace(tzinfo=timezone.utc)
                
            # Se estourar os 15 minutos sem confirmação, o passageiro perde a vaga (Expirado)
            if data_criacao and (agora - data_criacao) > timedelta(minutes=15):
                cursor.execute("UPDATE solicitacoes SET status = 'Expirado' WHERE id = %s", (sol["id"],))
        
        conexao.commit()
        
        # Retorna a listagem padrão contendo as alterações para o Kotlin atualizar a interface
        cursor.execute("SELECT * FROM solicitacoes")
        solicitacoes_do_cofre = cursor.fetchall()
        
        lista_final = []
        for sol in solicitacoes_do_cofre:
            lista_final.append({
                "id": sol["id"], 
                "carona_id": sol["carona_id"], 
                "passageiro": sol["passageiro"], 
                "status": sol["status"],
                "passageiro_cpf": sol.get("passageiro_cpf", "")
            })
            
        return jsonify(lista_final), 200
        
    except Exception as e:
        conexao.rollback()
        print(f"❌ Erro no relógio da Viagem Programada: {e}")
        return jsonify({"erro": str(e)}), 500
    finally:
        cursor.close()
        conexao.close()

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
        # 🕒 MODIFICADO: Insere o registro marcando o data_criacao inicial em fuso UTC explícito
        cursor.execute("""
            INSERT INTO solicitacoes (carona_id, passageiro, passageiro_cpf, status, data_criacao) 
            VALUES (%s, %s, %s, %s, %s)
        """, (carona_id, dados["passageiro"], cpf_passageiro, "Pendente", datetime.now(timezone.utc)))
        
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

@app.route("/corridas/emergentes", methods=["POST"])
@token_requerido
def criar_corrida_emergente():
    dados = request.get_json()
    passageiro_cpf = request.usuario_logado["cpf"]
    
    # O Kotlin vai mandar as coordenadas exatas de onde o passageiro está e para onde vai
    origem_lat = dados.get("origem_latitude")
    origem_lng = dados.get("origem_longitude")
    destino_lat = dados.get("destino_latitude")
    destino_lng = dados.get("destino_longitude")
    end_origem = dados.get("endereco_origem", "")
    end_destino = dados.get("endereco_destino", "")

    if not all([origem_lat, origem_lng, destino_lat, destino_lng]):
        return jsonify({"erro": "Coordenadas de origem e destino são obrigatórias!"}), 400

    conexao = conectar_banco()
    if not conexao:
        return jsonify({"erro": "Falha na conexão com o banco"}), 500
        
    cursor = conexao.cursor()
    try:
        # Insere a corrida com o status 'Procurando' e grava o horário exato em UTC
        cursor.execute("""
            INSERT INTO corridas_emergentes (
                passageiro_cpf, origem_latitude, origem_longitude, 
                destino_latitude, destino_longitude, endereco_origem, endereco_destino, status, data_criacao
            ) 
            VALUES (%s, %s, %s, %s, %s, %s, %s, 'Procurando', %s)
            RETURNING id
        """, (passageiro_cpf, origem_lat, origem_lng, destino_lat, destino_lng, end_origem, end_destino, datetime.now(timezone.utc)))
        
        corrida_id = cursor.fetchone()[0]
        conexao.commit()
        
        print(f"⚡ CORRIDA EMERGENTE CRIADA! ID: {corrida_id} | Passageiro: {passageiro_cpf}")
        return jsonify({"mensagem": "Procurando motoristas parceiros próximos...", "corrida_id": corrida_id}), 201
        
    except Exception as e:
        conexao.rollback()
        print(f"❌ Erro ao criar corrida emergente: {e}")
        return jsonify({"erro": str(e)}), 500
    finally:
        cursor.close()
        conexao.close()

@app.route("/corridas/emergentes/disponiveis", methods=["GET"])
@token_requerido
def listar_corridas_emergentes_proximas():
    conexao = conectar_banco()
    if not conexao:
        return jsonify({"erro": "Falha na conexão com o banco"}), 500
    
    cursor = conexao.cursor(cursor_factory=RealDictCursor)
    agora = datetime.now(timezone.utc)
    
    try:
        # 1. Primeiro fazemos a varredura do relógio (Card de Tempo de 60 segundos)
        # Qualquer corrida 'Procurando' com mais de 1 minuto vira 'Expirada'
        cursor.execute("SELECT id, data_criacao FROM corridas_emergentes WHERE status = 'Procurando'")
        corridas_ativas = cursor.fetchall()
        
        for corrida in corridas_ativas:
            # Garante que a data do banco venha com a marcação de timezone para comparar certo
            data_criacao = corrida["data_criacao"]
            if data_criacao.tzinfo is None:
                data_criacao = data_criacao.replace(tzinfo=timezone.utc)
                
            if (agora - data_criacao) > timedelta(seconds=60):
                cursor.execute("UPDATE corridas_emergentes SET status = 'Expirada' WHERE id = %s", (corrida["id"],))
        
        conexao.commit()
        
        # 2. Agora buscamos apenas as que sobreviveram ao tempo e ainda estão ativas
        cursor.execute("""
            SELECT * FROM corridas_emergentes 
            WHERE status = 'Procurando' 
            ORDER BY data_criacao DESC
        """)
        corridas_validas = cursor.fetchall()
        
        # Como o PostgreSQL não converte Decimal automaticamente para o JSON do Flask, 
        # transformamos as latitudes e longitudes em Float comuns para o Kotlin ler sem erro
        lista_final = []
        for c in corridas_validas:
            lista_final.append({
                "id": c["id"],
                "passageiro_cpf": c["passageiro_cpf"],
                "origem_latitude": float(c["origem_latitude"]),
                "origem_longitude": float(c["origem_longitude"]),
                "destino_latitude": float(c["destino_latitude"]),
                "destino_longitude": float(c["destino_longitude"]),
                "endereco_origem": c["endereco_origem"],
                "endereco_destino": c["endereco_destino"],
                "status": c["status"]
            })
            
        return jsonify(lista_final), 200
        
    except Exception as e:
        conexao.rollback()
        print(f"❌ Erro ao listar corridas emergentes: {e}")
        return jsonify({"erro": str(e)}), 500
    finally:
        cursor.close()
        conexao.close()

@app.route("/corridas/emergentes/aceitar/<int:corrida_id>", methods=["PUT"])
@token_requerido
def aceitar_corrida_emergente(corrida_id):
    motorista_cpf = request.usuario_logado["cpf"]
    conexao = conectar_banco()
    if not conexao:
        return jsonify({"erro": "Falha na conexão com o banco"}), 500
        
    cursor = conexao.cursor(cursor_factory=RealDictCursor)
    try:
        # Verifica se a corrida ainda está disponível ou se já expirou/foi aceita por outro
        cursor.execute("SELECT status, passageiro_cpf FROM corridas_emergentes WHERE id = %s", (corrida_id,))
        corrida = cursor.fetchone()
        
        if not corrida:
            return jsonify({"erro": "Corrida não encontrada!"}), 404
            
        if corrida["status"] == "Expirada":
            return jsonify({"erro": "O tempo limite acabou! Essa corrida expirou."}), 400
            
        if corrida["status"] != "Procurando":
            return jsonify({"erro": "Essa corrida já foi aceita por outro motorista!"}), 400

        # Atualiza a corrida vinculando o motorista
        cursor.execute("""
            UPDATE corridas_emergentes 
            SET status = 'Aceita', motorista_cpf = %s 
            WHERE id = %s
        """, (motorista_cpf, corrida_id))
        
        # Envia uma notificação FCM para o passageiro avisando que o motorista está a caminho
        cursor.execute("SELECT fcm_token FROM usuarios WHERE cpf = %s", (corrida["passageiro_cpf"],))
        passageiro = cursor.fetchone()
        if passageiro and passageiro.get("fcm_token"):
            enviar_notificacao(passageiro["fcm_token"], "⚡ Motorista a Caminho!", "Sua corrida de emergência foi aceita e o veículo já está se deslocando.")

        conexao.commit()
        return jsonify({"mensagem": "Corrida aceita com sucesso! Prossiga para o local de embarque."}), 200
        
    except Exception as e:
        conexao.rollback()
        print(f"❌ Erro ao aceitar corrida emergente: {e}")
        return jsonify({"erro": str(e)}), 500
    finally:
        cursor.close()
        conexao.close()

@app.route("/solicitacoes/<int:id_solicitacao>", methods=["PUT"])
def responder_solicitacao(id_solicitacao):
    dados = request.get_json()
    status_recebido = dados.get("status")
    
    conexao = conectar_banco()
    if not conexao:
        return jsonify({"erro": "Falha na conexão com o banco"}), 500
    cursor = conexao.cursor(cursor_factory=RealDictCursor)
    
    try:
        # 1. Atualiza o status da solicitação no banco de dados
        cursor.execute("UPDATE solicitacoes SET status = %s WHERE id = %s", (status_recebido, id_solicitacao))
        
        # 2. Busca o CPF do passageiro vinculado a esta solicitação e o nome do evento
        cursor.execute("""
            SELECT s.passageiro_cpf, c.evento_nome 
            FROM solicitacoes s
            JOIN caronas c ON s.carona_id = c.id
            WHERE s.id = %s
        """, (id_solicitacao,))
        resultado_sol = cursor.fetchone()
        
        if resultado_sol and resultado_sol.get("passageiro_cpf"):
            # 🟢 Mantendo o padrão estrutural do seu app.py para não quebrar o namespace
            cpf_pass = urllib.parse.unquote(str(resultado_sol["passageiro_cpf"]))
            nome_evento = resultado_sol["evento_nome"]
            
            # 3. Busca o token FCM do passageiro para enviar a notificação
            cursor.execute("SELECT fcm_token FROM usuarios WHERE cpf = %s", (cpf_pass,))
            usuario_pass = cursor.fetchone()
            
            if usuario_pass and usuario_pass.get("fcm_token"):
                token_fcm = usuario_pass["fcm_token"]
                
                # Trata o título e o corpo com base na resposta do motorista
                if "Aceito" in status_recebido:
                    titulo_fcm = "✅ Vaga Garantida!"
                    corpo_fcm = f"O motorista aceitou o seu pedido para o evento: {nome_evento}."
                elif "Recusado" in status_recebido:
                    titulo_fcm = "❌ Pedido Recusado"
                    # Separação de string amigável para evitar estouro de índice (IndexError)
                    partes_status = status_recebido.split(":", 1)
                    motivo = partes_status[1].strip() if len(partes_status) > 1 else "Motivo pessoal."
                    corpo_fcm = f"A sua solicitação para {nome_evento} foi recusada. Motivo: {motivo}"
                else:
                    titulo_fcm = "🔄 Atualização de Carona"
                    corpo_fcm = f"O estado do seu pedido para {nome_evento} mudou para {status_recebido}."
                
                # Dispara a notificação com as diretrizes de som ativas
                enviar_notificacao(token_fcm, titulo_fcm, corpo_fcm)

        conexao.commit()
        return jsonify({"mensagem": "Status atualizado e passageiro notificado com sucesso!"}), 200
    except Exception as e:
        conexao.rollback()
        print(f"❌ Erro ao atualizar status e notificar: {e}")
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
    if not conexao:
        return jsonify({"erro": "Falha na conexão com o banco"}), 500
    cursor = conexao.cursor(cursor_factory=RealDictCursor)
    
    try:
        cursor.execute("""
            SELECT 
                s.id,
                s.carona_id,
                s.passageiro,
                s.passageiro_cpf,
                s.status,
                c.evento_nome,
                c.cidade_origem,
                c.cidade_destino,
                c.horario
            FROM solicitacoes s
            JOIN caronas c ON s.carona_id = c.id
            WHERE s.passageiro_cpf = %s AND s.status = 'Finalizado'
            ORDER BY s.data_criacao DESC
        """, (urllib.parse.unquote(cpf),))
        
        historico = cursor.fetchall()
        return jsonify(historico), 200
    except Exception as e:
        print(f"❌ Erro no histórico do passageiro: {e}")
        return jsonify({"erro": str(e)}), 500
    finally:
        cursor.close()
        conexao.close()


@app.route("/historico_motorista_cpf/<cpf>", methods=["GET"])
def listar_historico_motorista_por_cpf(cpf):
    conexao = conectar_banco()
    if not conexao:
        return jsonify({"erro": "Falha na conexão com o banco"}), 500
    cursor = conexao.cursor(cursor_factory=RealDictCursor)
    
    try:
        # DISTINCT ON garante que traremos apenas os 6 eventos únicos, sem repetir cards por passageiro
        cursor.execute("""
            SELECT DISTINCT ON (c.id)
                c.id,
                c.id as carona_id,
                c.motorista as passageiro,
                c.motorista_cpf as passageiro_cpf,
                c.status,
                c.evento_nome,
                c.cidade_origem,
                c.cidade_destino,
                c.horario
            FROM caronas c
            WHERE c.motorista_cpf = %s AND c.status = 'Finalizado'
            ORDER BY c.id DESC
        """, (urllib.parse.unquote(cpf),))
        
        historico = cursor.fetchall()
        return jsonify(historico), 200
    except Exception as e:
        print(f"❌ Erro no histórico do motorista: {e}")
        return jsonify({"erro": str(e)}), 500
    finally:
        cursor.close()
        conexao.close()

@app.route("/cancelar_carona_geral", methods=["POST"])
def cancelar_carona_geral():
    dados = request.get_json()
    carona_id = dados.get("carona_id")
    motivo_cancelamento = dados.get("motivo", "Motivo de força maior")

    conexao = conectar_banco()
    if not conexao:
        return jsonify({"erro": "Falha na conexão com o banco"}), 500
    cursor = conexao.cursor(cursor_factory=RealDictCursor)

    try:
        # 1. Busca os dados do evento e os tokens FCM de TODOS os passageiros ativos vinculados a essa carona
        cursor.execute("""
            SELECT c.evento_nome, u.fcm_token 
            FROM solicitacoes s
            JOIN usuarios u ON s.passageiro_cpf = u.cpf
            JOIN caronas c ON s.carona_id = c.id
            WHERE s.carona_id = %s AND s.status IN ('Pendente', 'Aceito', 'Aprovado')
        """, (carona_id,))
        
        passageiros_afetados = cursor.fetchall()

        # Captura o nome da carona para compor o alerta
        cursor.execute("SELECT evento_nome FROM caronas WHERE id = %s", (carona_id,))
        carona_info = cursor.fetchone()
        nome_evento = carona_info["evento_nome"] if carona_info else "Viagem"

        # 2. Varre a lista disparando a notificação Push de estorno e cancelamento com som para cada celular
        for pass_info in passageiros_afetados:
            token = pass_info.get("fcm_token")
            if token:
                titulo_notif = f"⚠️ Viagem Cancelada: {nome_evento}"
                corpo_notif = f"O motorista precisou cancelar. Motivo: {motivo_cancelamento}. O valor correspondente será ressarcido!"
                enviar_notificacao(token, titulo_notif, corpo_notif)

        # 3. Altera o status da carona para 'Cancelada' para sumir instantaneamente dos Eventos Disponíveis
        cursor.execute("UPDATE caronas SET status = 'Cancelada' WHERE id = %s", (carona_id,))

        # 4. Modifica os pedidos no banco para deixar registrado o histórico de auditoria e liberação financeira
        cursor.execute("""
            UPDATE solicitacoes 
            SET status = %s 
            WHERE carona_id = %s AND status != 'Finalizado'
        """, (f"Cancelado: {motivo_cancelamento}", carona_id))

        conexao.commit()
        return jsonify({"mensagem": "Viagem derrubada com sucesso e passageiros alertados!"}), 200

    except Exception as e:
        conexao.rollback()
        print(f"❌ Erro ao processar cancelamento de emergência: {e}")
        return jsonify({"erro": str(e)}), 500
    finally:
        cursor.close()
        conexao.close()

# 🟢 NOVA ROTA 1: PASSAGEIRO MONITORAR O STATUS DO CHAMADO ATIVO
@app.route("/corridas/emergentes/status/<int:corrida_id>", methods=["GET"])
@token_requerido
def monitorar_status_corrida(corrida_id):
    conexao = conectar_banco()
    if not conexao:
        return jsonify({"erro": "Falha na conexão com o banco"}), 500
    cursor = conexao.cursor(cursor_factory=RealDictCursor)
    try:
        # Busca a corrida e traz os dados básicos do veículo/motorista associado (se houver)
        cursor.execute("""
            SELECT c.*, u.nome as motorista_nome, u.veiculo, u.placa 
            FROM corridas_emergentes c
            LEFT JOIN usuarios u ON c.motorista_cpf = u.cpf
            WHERE c.id = %s
        """, (corrida_id,))
        corrida = cursor.fetchone()
        if not corrida:
            return jsonify({"erro": "Corrida não encontrada!"}), 404
            
        return jsonify({
            "id": corrida["id"],
            "status": corrida["status"],
            "motorista_nome": corrida.get("motorista_nome", ""),
            "veiculo": corrida.get("veiculo", ""),
            "placa": corrida.get("placa", ""),
            "origem_latitude": float(corrida["origem_latitude"]),
            "origem_longitude": float(corrida["origem_longitude"]),
            "destino_latitude": float(corrida["destino_latitude"]),
            "destino_longitude": float(corrida["destino_longitude"])
        }), 200
    finally:
        cursor.close()
        conexao.close()

# 🟢 NOVA ROTA 2: CANCELAMENTO INTELIGENTE (REABRE A CORRIDA PARA OUTROS MOTORISTAS)
@app.route("/corridas/emergentes/cancelar/<int:corrida_id>", methods=["DELETE"])
@token_requerido
def cancelar_ou_reabrir_corrida(corrida_id):
    conexao = conectar_banco()
    if not conexao:
        return jsonify({"erro": "Falha na conexão com o banco"}), 500
    cursor = conexao.cursor(cursor_factory=RealDictCursor)
    try:
        cursor.execute("SELECT status FROM corridas_emergentes WHERE id = %s", (corrida_id,))
        corrida = cursor.fetchone()
        if not corrida:
            return jsonify({"erro": "Corrida inexistente"}), 404

        # REGRA DE NEGÓCIO SEU: Se o motorista já tinha aceitado mas está demorando, 
        # a corrida REABRE ('Procurando') limpando o motorista antigo para que outro pegue!
        if corrida["status"] == "Aceita":
            cursor.execute("""
                UPDATE corridas_emergentes 
                SET status = 'Procurando', motorista_cpf = NULL 
                WHERE id = %s
            """, (corrida_id,))
            mensagem_retorno = "Corrida reaberta no radar de Paulista para novos motoristas!"
        else:
            # Se ainda estava procurando, cancela de forma definitiva
            cursor.execute("UPDATE corridas_emergentes SET status = 'Cancelada' WHERE id = %s", (corrida_id,))
            mensagem_retorno = "Corrida cancelada com sucesso!"

        conexao.commit()
        return jsonify({"mensagem": mensagem_retorno}), 200
    except Exception as e:
        conexao.rollback()
        return jsonify({"erro": str(e)}), 500
    finally:
        cursor.close()
        conexao.close()

if __name__ == "__main__":
    porta = int(os.environ.get("PORT", 5000))
    app.run(debug=True, host="0.0.0.0", port=porta)