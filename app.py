import os
import urllib.parse
import psycopg2
from psycopg2.extras import RealDictCursor
from flask import Flask, jsonify, request
import firebase_admin
from firebase_admin import credentials, messaging
import json
from datetime import datetime, timedelta, timezone
import jwt
from functools import wraps
from datetime import datetime
import pytz

# Configura o fuso horário de Brasília
fuso_brasilia = pytz.timezone('America/Sao_Paulo')

# Garante a hora certa de Brasília, independente de onde o servidor está rodando
data_atual = datetime.now(fuso_brasilia).strftime('%d/%M/%Y %H:%M') 
# Se o seu banco salvar como timestamp/data pura, use: datetime.now(fuso_brasilia)

app = Flask(__name__)

# 🟢 CERTIFIQUE-SE DE QUE ESTA LINHA EXISTE AQUI ANTES DOS PLUGUES:
JWT_SECRET = os.environ.get("JWT_SECRET", "uma_chave_secreta_super_robusta_e_longa_para_desenvolvimento")

# --- CONFIGURAÇÃO SEGURA DO FIREBASE ---
# --- CONFIGURAÇÃO SEGURA DO FIREBASE (COM FALLBACK LOCAL) ---
firebase_config_str = os.environ.get("FIREBASE_CONFIG_JSON")

if firebase_config_str:
    try:
        firebase_config = json.loads(firebase_config_str)
        cred = credentials.Certificate(firebase_config)
        firebase_admin.initialize_app(cred)
        print("✅ Firebase inicializado com sucesso via Nuvem!")
    except Exception as e:
        print(f"❌ Erro ao inicializar Firebase: {e}")
elif os.path.exists("firebase-key.json"):
    try:
        # 🟢 Se não achar na nuvem, lê o seu arquivo local do VS Code!
        cred = credentials.Certificate("firebase-key.json")
        firebase_admin.initialize_app(cred)
        print("✅ Firebase inicializado com sucesso via arquivo local firebase-key.json!")
    except Exception as e:
        print(f"❌ Erro ao inicializar Firebase local: {e}")
else:
    print("⚠️ AVISO: Nenhuma credencial do Firebase encontrada!")

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
    # 🟢 AUTOMÁTICO: Busca a credencial da memória segura do servidor
    DATABASE_URL = os.environ.get("DATABASE_URL")
    
    if not DATABASE_URL:
        # Padrão limpo para o GitHub. Para testar local sem ligar o Postgres no PC,
        # basta rodar o comando do terminal com a URL do Render antes de iniciar o app!
        DATABASE_URL = "postgresql://usuario_local:senha_local@localhost:5432/transporte_db_novo"
        
    try:
        conexao = psycopg2.connect(DATABASE_URL)
        return conexao
    except Exception as e:
        print(f"Erro ao conectar no banco: {e}")
        return None

def enviar_notificacao(token, titulo, corpo):
    try:
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
            android=android_alert
        )
        messaging.send(message)
        print("✅ Notificação enviada com diretrizes de som ativa!")
    except Exception as e:
        print(f"Erro ao enviar notificação: {e}")
        
def criar_tabelas():
    conexao = conectar_banco()
    if not conexao:
        print("⚠️ AVISO: Não foi possível estruturar as tabelas pois o banco de dados está offline.")
        return
        
    cursor = conexao.cursor()
    try:
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS usuarios (
                cpf TEXT PRIMARY KEY, nome TEXT NOT NULL, email TEXT UNIQUE NOT NULL,
                telefone TEXT NOT NULL, veiculo TEXT, placa TEXT, senha TEXT NOT NULL,
                vagas TEXT, rua TEXT, numero TEXT, complemento TEXT, bairro TEXT,
                cidade TEXT, estado TEXT, cep TEXT
            )
        """)
        cursor.execute("ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS usuario TEXT UNIQUE;")
        cursor.execute("ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS fcm_token TEXT;")
        cursor.execute("ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS data_cadastro TEXT DEFAULT '15/06/2026';")
        cursor.execute("ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS corridas_realizadas INTEGER DEFAULT 0;")
        cursor.execute("ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS passageiros_conduzidos INTEGER DEFAULT 0;")
        cursor.execute("ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS vagas_ofertadas INTEGER DEFAULT 0;")
        cursor.execute("ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS modalidade_ativa TEXT DEFAULT 'Programada';")

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS caronas (
                id SERIAL PRIMARY KEY, evento_nome TEXT, cidade_origem TEXT, endereco_origem TEXT,
                cidade_destino TEXT, endereco_destino TEXT, horario TEXT, vagas TEXT, motorista TEXT, status TEXT DEFAULT 'Aberta'
            )
        """)
        cursor.execute("ALTER TABLE caronas ADD COLUMN IF NOT EXISTS status TEXT DEFAULT 'Aberta';")
        cursor.execute("ALTER TABLE caronas ADD COLUMN IF NOT EXISTS motorista_cpf TEXT;")

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS solicitacoes (
                id SERIAL PRIMARY KEY, carona_id INTEGER, passageiro TEXT, status TEXT
            )
        """)
        cursor.execute("ALTER TABLE solicitacoes ADD COLUMN IF NOT EXISTS passageiro_cpf TEXT;")
        cursor.execute("ALTER TABLE solicitacoes ADD COLUMN IF NOT EXISTS data_criacao TIMESTAMP DEFAULT CURRENT_TIMESTAMP;")

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS codigos_recuperacao (
                email TEXT PRIMARY KEY, codigo TEXT NOT NULL, expiracao TIMESTAMP NOT NULL
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS corridas_emergentes (
                id SERIAL PRIMARY KEY, passageiro_cpf TEXT NOT NULL, motorista_cpf TEXT,
                origem_latitude NUMERIC NOT NULL, origem_longitude NUMERIC NOT NULL,
                destino_latitude NUMERIC NOT NULL, destino_longitude NUMERIC NOT NULL,
                endereco_origem TEXT, endereco_destino TEXT, status TEXT DEFAULT 'Procurando',
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

# =====================================================================
# ⚡ MODO UBER / CORRIDAS EMERGENCIAIS (VERSÃO SEGURA & BLINDADA)
# =====================================================================

@app.route("/corridas/emergentes", methods=["POST"])
@token_requerido
def criar_corrida_emergente():
    dados = request.get_json()
    passageiro_cpf = request.usuario_logado["cpf"]
    origem_lat = dados.get("origem_latitude")
    origem_lng = dados.get("origem_longitude")
    destino_lat = dados.get("destino_latitude")
    destino_lng = dados.get("destino_longitude")
    veiculo_tipo = dados.get("veiculo_tipo", "Carro")
    
    if not all([origem_lat,裝rigem_lng, destino_lat, destino_lng]):
        return jsonify({"erro": "Parâmetros incorretos ou incompletos."}), 400

    conexao = conectar_banco()
    cursor = conexao.cursor()
    try:
        cursor.execute("""
            INSERT INTO corridas_emergentes (passageiro_cpf, origem_latitude, origem_longitude, destino_latitude, destino_longitude, endereco_origem, endereco_destino, status, veiculo_tipo, data_criacao) 
            VALUES (%s, %s, %s, %s, %s, %s, %s, 'Procurando', %s, %s) RETURNING id
        """, (passageiro_cpf, origem_lat, origem_lng, destino_lat, destino_lng, dados.get("endereco_origem", ""), dados.get("endereco_destino", ""), veiculo_tipo, datetime.now(fuso_brasilia)))
        corrida_id = cursor.fetchone()[0]
        conexao.commit()
        return jsonify({"mensagem": f"Procurando motoristas de {veiculo_tipo}...", "corrida_id": corrida_id}), 201
    except Exception as e:
        conexao.rollback()
        return jsonify({"erro": "Erro interno ao processar solicitação."}), 500
    finally:
        cursor.close()
        conexao.close()

@app.route("/corridas/emergentes/disponiveis", methods=["GET"])
@token_requerido
def listar_corridas_emergentes_proximas():
    motorista_cpf = request.usuario_logado["cpf"]
    
    conexao = conectar_banco()
    cursor = conexao.cursor(cursor_factory=RealDictCursor)
    agora = datetime.now(fuso_brasilia)
    
    try:
        cursor.execute("SELECT id, data_criacao FROM corridas_emergentes WHERE status = 'Procurando'")
        for corrida in cursor.fetchall():
            data_criacao = corrida["data_criacao"]
            if data_criacao.tzinfo is None:
                data_criacao = fuso_brasilia.localize(data_criacao)
            else:
                data_criacao = data_criacao.astimezone(fuso_brasilia)
                
            if (agora - data_criacao) > timedelta(seconds=600):
                cursor.execute("UPDATE corridas_emergentes SET status = 'Expirada' WHERE id = %s", (corrida["id"],))
        conexao.commit()
        
        cursor.execute("SELECT veiculo FROM usuarios WHERE cpf = %s", (motorista_cpf,))
        usuario_mot = cursor.fetchone()
        
        filtro_veiculo = "Carro"
        if usuario_mot and usuario_mot["veiculo"] and usuario_mot["veiculo"].startswith("Moto"):
            filtro_veiculo = "Moto"

        cursor.execute("""
            SELECT * FROM corridas_emergentes 
            WHERE status = 'Procurando' AND veiculo_tipo = %s 
            ORDER BY data_criacao DESC
        """, (filtro_veiculo,))
        
        lista_final = []
        for c in cursor.fetchall():
            lista_final.append({
                "id": c["id"], "passageiro_cpf": c["passageiro_cpf"], "origem_latitude": float(c["origem_latitude"]),
                "origem_longitude": float(c["origem_longitude"]), "destino_latitude": float(c["destino_latitude"]),
                "destino_longitude": float(c["destino_longitude"]), "endereco_origem": c["endereco_origem"], "endereco_destino": c["endereco_destino"], "status": c["status"]
            })
        return jsonify(lista_final), 200
    except Exception as e:
        print(f"Erro no radar: {e}")
        return jsonify({"erro": str(e)}), 500
    finally:
        cursor.close()
        conexao.close()

@app.route("/corridas/emergentes/aceitar/<int:corrida_id>", methods=["PUT"])
@token_requerido
def aceitar_corrida_emergente(corrida_id):
    motorista_cpf = request.usuario_logado["cpf"]
    conexao = conectar_banco()
    cursor = conexao.cursor(cursor_factory=RealDictCursor)
    try:
        cursor.execute("SELECT status, passageiro_cpf FROM corridas_emergentes WHERE id = %s", (corrida_id,))
        corrida = cursor.fetchone()
        if not corrida or corrida["status"] != "Procurando":
            return jsonify({"erro": "Corrida indisponível ou já aceita por outro parceiro."}), 400

        cursor.execute("UPDATE corridas_emergentes SET status = 'Aceita', motorista_cpf = %s WHERE id = %s", (motorista_cpf, corrida_id))
        cursor.execute("SELECT fcm_token FROM usuarios WHERE cpf = %s", (corrida["passageiro_cpf"],))
        passageiro = cursor.fetchone()
        if passageiro and passageiro.get("fcm_token"):
            enviar_notificacao(passageiro["fcm_token"], "⚡ Motorista a Caminho!", "Sua corrida de emergência foi aceita.")
        conexao.commit()
        return jsonify({"mensagem": "Corrida aceita com sucesso!"}), 200
    finally:
        cursor.close()
        conexao.close()

@app.route("/corridas/emergentes/status/<int:corrida_id>", methods=["GET"])
@token_requerido
def monitorar_status_corrida(corrida_id):
    usuario_id = request.usuario_logado["cpf"] # Captura quem está chamando a API pelo JWT
    conexao = conectar_banco()
    cursor = conexao.cursor(cursor_factory=RealDictCursor)
    try:
        cursor.execute("""
            SELECT c.*, u.nome as motorista_nome, u.veiculo, u.placa 
            FROM corridas_emergentes c LEFT JOIN usuarios u ON c.motorista_cpf = u.cpf WHERE c.id = %s
        """, (corrida_id,))
        corrida = cursor.fetchone()
        
        if not corrida:
            return jsonify({"erro": "Corrida não encontrada."}), 404
            
        # 🛡️ CONTROLE DE ACESSO (Broken Object Level Authorization - BOLA Mitigação):
        # Bloqueia a requisição se o CPF do token não for nem o do passageiro e nem o do motorista da corrida
        if usuario_id != corrida["passageiro_cpf"] and usuario_id != corrida["motorista_cpf"]:
            return jsonify({"erro": "Acesso negado. Você não faz parte desta corrida."}), 403

        return jsonify({
            "id": corrida["id"], "status": corrida["status"], "motorista_nome": corrida.get("motorista_nome", ""),
            "veiculo": corrida.get("veiculo", ""), "placa": corrida.get("placa", ""),
            "origem_latitude": float(corrida["origem_latitude"]), "origem_longitude": float(corrida["origem_longitude"]),
            "destino_latitude": float(corrida["destino_latitude"]), "destino_longitude": float(corrida["destino_longitude"])
        }), 200
    finally:
        cursor.close()
        conexao.close()

# 自由 ROTA ATUALIZADA: Altera o estado da viagem emergencial e contabiliza no perfil dos usuários
@app.route("/corridas/emergentes/atualizar_status/<int:corrida_id>", methods=["PUT"])
@token_requerido
def atualizar_status_viagem_emergente(corrida_id):
    motorista_cpf = request.usuario_logado["cpf"]
    dados = request.get_json()
    novo_status = dados.get("status")

    if novo_status not in ["Em Viagem", "Finalizada"]:
        return jsonify({"erro": "Estado de transição inválido."}), 400

    conexao = conectar_banco()
    cursor = conexao.cursor(cursor_factory=RealDictCursor)
    try:
        # 🟢 ALTERAÇÃO 1: Adicionado 'passageiro_cpf' no SELECT para rastrearmos quem é o passageiro desta corrida
        cursor.execute("SELECT motorista_cpf, status, passageiro_cpf FROM corridas_emergentes WHERE id = %s", (corrida_id,))
        corrida = cursor.fetchone()

        if not corrida:
            return jsonify({"erro": "Corrida inexistente."}), 404

        # 🛡️ Garante que apenas o motorista legítimo que aceitou a corrida possa iniciar/finalizar
        if corrida["motorista_cpf"] != motorista_cpf:
            return jsonify({"erro": "Operação não authorized para o seu usuário."}), 403

        # Executa a atualização do status da corrida emergencial
        cursor.execute("UPDATE corridas_emergentes SET status = %s WHERE id = %s", (novo_status, corrida_id))

        # 🟢 ALTERAÇÃO 2: Se a corrida foi Finalizada com sucesso, incrementa as métricas unificadas na tabela de usuários
        if novo_status == "Finalizada":
            # 🟢 ALTERAÇÃO 1: Descobre o tipo de veículo do motorista para calcular o multiplicador de vagas_ofertadas
            cursor.execute("SELECT veiculo FROM usuarios WHERE cpf = %s", (motorista_cpf,))
            usuario_mot = cursor.fetchone()
            
            # Se começar com Moto soma 1, senão soma 4 (Carros, Vans, etc.)
            vagas_a_somar = 1
            if usuario_mot and usuario_mot["veiculo"] and not usuario_mot["veiculo"].startswith("Moto"):
                vagas_a_somar = 4

            # 1. Soma +1 corrida, +1 passageiro e o multiplicador correto de vagas_ofertadas para o MOTORISTA
            cursor.execute("""
                UPDATE usuarios 
                SET corridas_realizadas = corridas_realizadas + 1, 
                    passageiros_conduzidos = passageiros_conduzidos + 1,
                    vagas_ofertadas = vagas_ofertadas + %s
                WHERE cpf = %s
            """, (vagas_a_somar, motorista_cpf))

            # 2. Soma +1 corrida realizada para o PASSAGEIRO correspondente
            cursor.execute("""
                UPDATE usuarios 
                SET corridas_realizadas = corridas_realizadas + 1 
                WHERE cpf = %s
            """, (corrida["passageiro_cpf"],))

        conexao.commit()
        return jsonify({"mensagem": f"Corrida atualizada para {novo_status} com sucesso e contabilizada!"}), 200
    except Exception as e:
        conexao.rollback()
        return jsonify({"erro": "Erro ao processar transição e métricas no banco."}), 500
    finally:
        cursor.close()
        conexao.close()

@app.route("/corridas/emergentes/cancelar/<int:corrida_id>", methods=["DELETE"])
@token_requerido
def cancelar_ou_reabrir_corrida(corrida_id):
    usuario_cpf = request.usuario_logado["cpf"]
    conexao = conectar_banco()
    cursor = conexao.cursor(cursor_factory=RealDictCursor)
    try:
        cursor.execute("SELECT status, passageiro_cpf, motorista_cpf FROM corridas_emergentes WHERE id = %s", (corrida_id,))
        corrida = cursor.fetchone()
        
        if not corrida:
            return jsonify({"erro": "Corrida não encontrada."}), 404
            
        # 🛡️ Só permite o cancelamento se quem chamou for o passageiro dono ou o motorista vinculado
        if usuario_cpf != corrida["passageiro_cpf"] and usuario_cpf != corrida["motorista_cpf"]:
            return jsonify({"erro": "Ação não autorizada."}), 403

        # 🟢 ALTERAÇÃO CIRÚRGICA: Se for cancelada dentro da tolerância, muda para o status definitivo correto
        if corrida["status"] == "Aceita":
            cursor.execute("UPDATE corridas_emergentes SET status = 'Cancelada pelo passageiro', motorista_cpf = NULL WHERE id = %s", (corrida_id,))
            msg = "Corrida cancelada pelo passageiro!"
        else:
            cursor.execute("UPDATE corridas_emergentes SET status = 'Cancelada pelo passageiro' WHERE id = %s", (corrida_id,))
            msg = "Corrida cancelada pelo passageiro!"
        conexao.commit()
        return jsonify({"mensagem": msg}), 200
    except Exception as e:
        conexao.rollback()
        return jsonify({"erro": str(e)}), 500
    finally:
        cursor.close()
        conexao.close()

# =====================================================================
# ⚡ ENDPOINTS PARA HISTÓRICO DE CORRIDAS EMERGENCIAIS CONCLUÍDAS
# =====================================================================

@app.route("/corridas/emergentes/historico_passageiro/<cpf>", methods=["GET"])
def obter_historico_emergente_passageiro(cpf):
    conexao = conectar_banco()
    cursor = conexao.cursor(cursor_factory=RealDictCursor)
    try:
        # Busca todas as corridas emergentes finalizadas associadas ao CPF do passageiro
        cursor.execute("""
            SELECT id, endereco_origem, endereco_destino, status,
                   to_char(data_criacao, 'DD/MM/YYYY HH24:MI') as data_criacao
            FROM corridas_emergentes
            WHERE passageiro_cpf = %s AND status = 'Finalizada'
            ORDER BY data_criacao DESC
        """, (cpf,))
        return jsonify(cursor.fetchall()), 200
    except Exception as e:
        print(f"❌ Erro ao buscar historico emergencial do passageiro: {e}")
        return jsonify({"erro": str(e)}), 500
    finally:
        cursor.close()
        conexao.close()

@app.route("/corridas/emergentes/historico_motorista/<cpf>", methods=["GET"])
def obter_historico_emergente_motorista(cpf):
    conexao = conectar_banco()
    cursor = conexao.cursor(cursor_factory=RealDictCursor)
    try:
        # Busca as corridas finalizadas do motorista trazendo o nome do passageiro correspondente
        cursor.execute("""
            SELECT c.id, c.endereco_origem, c.endereco_destino, c.status,
                   to_char(c.data_criacao, 'DD/MM/YYYY HH24:MI') as data_criacao,
                   u.nome as passageiro_nome
            FROM corridas_emergentes c
            LEFT JOIN usuarios u ON c.passageiro_cpf = u.cpf
            WHERE c.motorista_cpf = %s AND c.status = 'Finalizada'
            ORDER BY c.data_criacao DESC
        """, (cpf,))
        return jsonify(cursor.fetchall()), 200
    except Exception as e:
        print(f"❌ Erro ao buscar historico emergencial do motorista: {e}")
        return jsonify({"erro": str(e)}), 500
    finally:
        cursor.close()
        conexao.close()
        
# =====================================================================
# 🔌 PLUGUES DOS CONTROLADORES EXTERNOS (MVC)
# =====================================================================
from controllers.solicitacao_controller import configurar_rotas_solicitacao
from controllers.carona_controller import configurar_rotas_carona
from controllers.usuario_controller import configurar_rotas_usuario

# 🟢 CERTIFIQUE-SE DE QUE ESTA LINHA EXISTE AQUI ANTES DOS PLUGUES:
#JWT_SECRET = os.environ.get("JWT_SECRET", "uma_chave_secreta_super_robusta_e_longa_para_desenvolvimento")

configurar_rotas_solicitacao(app, conectar_banco, enviar_notificacao)
configurar_rotas_carona(app, conectar_banco)
configurar_rotas_usuario(app, conectar_banco, token_requerido, JWT_SECRET)

if __name__ == "__main__":
    porta = int(os.environ.get("PORT", 5000))
    app.run(debug=True, host="0.0.0.0", port=porta)