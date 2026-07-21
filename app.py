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
#from database import conectar_banco
from controllers.database import conectar_banco
from dotenv import load_dotenv
load_dotenv()

# 1. Instancia o aplicativo Flask primeiro
app = Flask(__name__)

# 2. Importações dos módulos e controllers do projeto que dependem do 'app'
from controllers.database import conectar_banco, inicializar_banco
from controllers.pagamento_emergente_controller import configurar_rotas_pagamento_emergente

# Configurações de segurança e chaves
JWT_SECRET = os.environ.get("JWT_SECRET", "uma_chave_secreta_super_robusta_e_longa_para_desenvolvimento")
try:
    # Tenta obter a instância, se já existir, ele não faz nada (evita o erro do terminal)
    firebase_admin.get_app()
    print("✅ Firebase já inicializado.")
except ValueError:
    # Se não existir, inicializa
    firebase_config_str = os.environ.get("FIREBASE_CONFIG_JSON")
    if firebase_config_str:
        firebase_config = json.loads(firebase_config_str)
        cred = credentials.Certificate(firebase_config)
        firebase_admin.initialize_app(cred)
    elif os.path.exists("firebase-key.json"):
        cred = credentials.Certificate("firebase-key.json")
        firebase_admin.initialize_app(cred)
    print("✅ Firebase inicializado com sucesso!")
    pass

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
        except jwt.InvalidTokenError as e:
            print(f"❌ ERRO NO TOKEN JWT: {e}")
            return jsonify({"erro": "Token inválido ou corrompido!"}), 401
        except Exception as e:
            print(f"❌ EXCEÇÃO GERAL NO TOKEN: {e}")
            return jsonify({"erro": "Erro na verificação de acesso.", "detalhe": str(e)}), 401

        return f(*args, **kwargs)
    return decorated

def enviar_notificacao(token, titulo, corpo):
    try:
        android_alert = messaging.AndroidConfig(
            priority='high',
            notification=messaging.AndroidNotification(
                sound='default',
                default_sound=True,
                channel_id='canal_caronas_urgente_v2' # 🟢 AGORA O PYTHON EXIGE O CANAL BARULHENTO!
            )
        )
        message = messaging.Message(
            notification=messaging.Notification(title=titulo, body=corpo),
            token=token,
            android=android_alert
        )
        messaging.send(message)
        print("✅ Notificação enviada com canal urgente vinculado!")
    except Exception as e:
        print(f"Erro ao enviar notificação: {e}")

def criar_tabelas():
    conexao = conectar_banco()
    if not conexao:
        print("⚠️ AVISO: Não foi possível estruturar as tabelas pois o banco de dados está offline.")
        return
        
    cursor = conexao.cursor()
    try:
        # 1. Tabela de Usuários
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
        cursor.execute("ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS bloqueado BOOLEAN DEFAULT FALSE;")

        # 2. Tabela de Caronas Programadas
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS caronas (
                id SERIAL PRIMARY KEY, evento_nome TEXT, cidade_origem TEXT, endereco_origem TEXT,
                cidade_destino TEXT, endereco_destino TEXT, horario TEXT, vagas TEXT, motorista TEXT, status TEXT DEFAULT 'Aberta'
            )
        """)
        cursor.execute("ALTER TABLE caronas ADD COLUMN IF NOT EXISTS status TEXT DEFAULT 'Aberta';")
        cursor.execute("ALTER TABLE caronas ADD COLUMN IF NOT EXISTS motorista_cpf TEXT;")

        # 3. Tabela de Solicitações
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS solicitacoes (
                id SERIAL PRIMARY KEY, carona_id INTEGER, passageiro TEXT, status TEXT
            )
        """)
        cursor.execute("ALTER TABLE solicitacoes ADD COLUMN IF NOT EXISTS passageiro_cpf TEXT;")
        cursor.execute("ALTER TABLE solicitacoes ADD COLUMN IF NOT EXISTS data_criacao TIMESTAMP DEFAULT CURRENT_TIMESTAMP;")
        cursor.execute("ALTER TABLE solicitacoes ADD COLUMN IF NOT EXISTS data_finalizacao TIMESTAMP;")

        # 4. Tabela de Códigos de Recuperação
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS codigos_recuperacao (
                email TEXT PRIMARY KEY, codigo TEXT NOT NULL, expiracao TIMESTAMP NOT NULL
            )
        """)

        # 5. Tabela de Corridas Emergenciais
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS corridas_emergentes (
                id SERIAL PRIMARY KEY, passageiro_cpf TEXT NOT NULL, motorista_cpf TEXT,
                origem_latitude NUMERIC NOT NULL, origem_longitude NUMERIC NOT NULL,
                destino_latitude NUMERIC NOT NULL, destino_longitude NUMERIC NOT NULL,
                endereco_origem TEXT, endereco_destino TEXT, status TEXT DEFAULT 'Procurando',
                data_criacao TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
            )
        """)
        cursor.execute("ALTER TABLE corridas_emergentes ADD COLUMN IF NOT EXISTS veiculo_tipo TEXT DEFAULT 'Carro';")
        cursor.execute("ALTER TABLE corridas_emergentes ADD COLUMN IF NOT EXISTS data_finalizacao TIMESTAMP WITH TIME ZONE;")
        cursor.execute("ALTER TABLE corridas_emergentes ADD COLUMN IF NOT EXISTS motorista_latitude NUMERIC;")
        cursor.execute("ALTER TABLE corridas_emergentes ADD COLUMN IF NOT EXISTS motorista_longitude NUMERIC;")
        cursor.execute("ALTER TABLE corridas_emergentes ADD COLUMN IF NOT EXISTS pago BOOLEAN DEFAULT TRUE;")
        cursor.execute("ALTER TABLE corridas_emergentes ADD COLUMN IF NOT EXISTS valor_corrida NUMERIC DEFAULT 0.0;")

        # 6. Tabela de Débitos para Quitação via Pix Mercado Pago (UNIFICADA E LIMPA)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS debitos_passageiros (
                id SERIAL PRIMARY KEY,
                passageiro_cpf VARCHAR(14) NOT NULL,
                corrida_id INT,
                valor_pendente NUMERIC(10,2) DEFAULT 0.00,
                valor_cobrado NUMERIC(10,2) DEFAULT 0.01,
                payment_id VARCHAR(100) UNIQUE,
                status VARCHAR(20) DEFAULT 'pendente',
                data_criacao TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
            );
        """)

        conexao.commit()
        print("✅ Tabelas, colunas e modo de pagamento verificados com sucesso!")
    except Exception as e:
        conexao.rollback()
        print(f"❌ Erro ao estruturar tabelas no PostgreSQL: {e}")
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
    # 🟢 ALTERAÇÃO 1: Captura se o passageiro quer 'Carro' ou 'Moto' enviado pelo aplicativo
    veiculo_tipo = dados.get("veiculo_tipo", "Carro")
    
    if not all([origem_lat, origem_lng, destino_lat, destino_lng]):
        return jsonify({"erro": "Parâmetros incorretos ou incompletos."}), 400

    conexao = conectar_banco()
    cursor = conexao.cursor()
    try:
        # 🟢 ALTERAÇÃO 2: Grava o tipo solicitado na nova coluna 'veiculo_tipo'
        cursor.execute("""
            INSERT INTO corridas_emergentes (passageiro_cpf, origem_latitude, origem_longitude, destino_latitude, destino_longitude, endereco_origem, endereco_destino, status, veiculo_tipo, data_criacao) 
            VALUES (%s, %s, %s, %s, %s, %s, %s, 'Procurando', %s, %s) RETURNING id
        """, (passageiro_cpf, origem_lat, origem_lng, destino_lat, destino_lng, dados.get("endereco_origem", ""), dados.get("endereco_destino", ""), veiculo_tipo, datetime.now(timezone.utc))) # timezone.utc
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
    # Captura o CPF do motorista logado que está a pedir a lista do radar
    motorista_cpf = request.usuario_logado["cpf"]
    
    conexao = conectar_banco()
    cursor = conexao.cursor(cursor_factory=RealDictCursor)
    try:
        # 1. Pega a hora exata do Brasil e volta 10 minutos (600 segundos) no relógio
        limite_tempo = datetime.now(timezone.utc) - timedelta(minutes=10)

        # 2. Atualiza todos os chamados que passaram do limite de uma vez só!
        cursor.execute("""
            UPDATE corridas_emergentes 
            SET status = 'Expirada' 
            WHERE status = 'Procurando' AND data_criacao < %s
        """, (limite_tempo,))
        conexao.commit()
        
        # 3. Descobre qual é o tipo de veículo real deste motorista (Carro ou Moto)
        cursor.execute("SELECT veiculo FROM usuarios WHERE cpf = %s", (motorista_cpf,))
        usuario_mot = cursor.fetchone()
        
        filtro_veiculo = "Carro"
        if usuario_mot and usuario_mot["veiculo"] and usuario_mot["veiculo"].startswith("Moto"):
            filtro_veiculo = "Moto"

        # 4. O Filtro SQL agora só traz chamados válidos
        cursor.execute("""
            SELECT * FROM corridas_emergentes 
            WHERE status = 'Procurando' AND veiculo_tipo = %s 
            ORDER BY data_criacao DESC
        """, (filtro_veiculo,))
        
        grid_final = []
        for c in cursor.fetchall():
            grid_final.append({
                "id": c["id"], "passageiro_cpf": c["passageiro_cpf"], "origem_latitude": float(c["origem_latitude"]),
                "origem_longitude": float(c["origem_longitude"]), "destino_latitude": float(c["destino_latitude"]),
                "destino_longitude": float(c["destino_longitude"]), "endereco_origem": c["endereco_origem"], "endereco_destino": c["endereco_destino"], "status": c["status"]
            })
        return jsonify(grid_final), 200
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
    usuario_id = request.usuario_logado["cpf"]
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
        if usuario_id != corrida["passageiro_cpf"] and usuario_id != corrida["motorista_cpf"]:
            return jsonify({"erro": "Acesso negado. Você não faz parte desta corrida."}), 403

        return jsonify({
            "id": corrida["id"], "status": corrida["status"], "motorista_nome": corrida.get("motorista_nome", ""),
            "veiculo": corrida.get("veiculo", ""), "placa": corrida.get("placa", ""),
            "origem_latitude": float(corrida["origem_latitude"]), "origem_longitude": float(corrida["origem_longitude"]),
            "destino_latitude": float(corrida["destino_latitude"]), "destino_longitude": float(corrida["destino_longitude"]),
            "motorista_latitude": float(corrida["motorista_latitude"]) if corrida.get("motorista_latitude") else float(corrida["origem_latitude"]),
            "motorista_longitude": float(corrida["motorista_longitude"]) if corrida.get("motorista_longitude") else float(corrida["origem_longitude"])
        }), 200
    finally:
        cursor.close()
        conexao.close()

# ROTA ATUALIZADA: Altera o estado da viagem emergencial e contabiliza no perfil dos usuários
@app.route("/corridas/emergentes/atualizar_status/<int:corrida_id>", methods=["PUT"])
@token_requerido
def atualizar_status_viagem_emergente(corrida_id):
    motorista_cpf = request.usuario_logado["cpf"]
    dados = request.get_json()
    novo_status = dados.get("status")
    
    # 🟢 CORREÇÃO: O servidor agora escuta as variáveis financeiras enviadas pelo Android
    pago = dados.get("pago", True)
    valor_corrida = dados.get("valor_corrida", 0.0)

    if novo_status not in ["Em Viagem", "Finalizada"]:
        return jsonify({"erro": "Estado de transição inválido."}), 400

    conexao = conectar_banco()
    cursor = conexao.cursor(cursor_factory=RealDictCursor)
    
    try:
        cursor.execute("SELECT motorista_cpf, status, passageiro_cpf FROM corridas_emergentes WHERE id = %s", (corrida_id,))
        corrida = cursor.fetchone()

        if not corrida:
            return jsonify({"erro": "Corrida inexistente."}), 404

        if corrida["motorista_cpf"] != motorista_cpf:
            return jsonify({"erro": "Operação não autorizada para o seu usuário."}), 403

        # 🟢 CORREÇÃO: Grava o calote e o valor da corrida no banco de dados
        if novo_status == "Finalizada":
            cursor.execute("""
                UPDATE corridas_emergentes 
                SET status = %s, data_finalizacao = %s, pago = %s, valor_corrida = %s 
                WHERE id = %s
            """, (novo_status, datetime.now(timezone.utc), pago, valor_corrida, corrida_id))
            
            # 🟢 O "Pulo do Gato": Se o pagamento for falso, bloqueia o usuário imediatamente!
            if not pago:
                cursor.execute("UPDATE usuarios SET bloqueado = TRUE WHERE cpf = %s", (corrida["passageiro_cpf"],))
                print(f"⚠️ Passageiro {corrida['passageiro_cpf']} bloqueado por não pagamento.")
        else:
            cursor.execute("UPDATE corridas_emergentes SET status = %s WHERE id = %s", (novo_status, corrida_id))

        if novo_status == "Finalizada":
            cursor.execute("SELECT veiculo FROM usuarios WHERE cpf = %s", (motorista_cpf,))
            usuario_mot = cursor.fetchone()
            
            vagas_a_somar = 1
            if usuario_mot and usuario_mot["veiculo"] and not usuario_mot["veiculo"].startswith("Moto"):
                vagas_a_somar = 4

            cursor.execute("""
                UPDATE usuarios 
                SET corridas_realizadas = corridas_realizadas + 1, 
                    passageiros_conduzidos = passageiros_conduzidos + 1,
                    vagas_ofertadas = vagas_ofertadas + %s
                WHERE cpf = %s
            """, (vagas_a_somar, motorista_cpf))

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
            
        if usuario_cpf != corrida["passageiro_cpf"] and usuario_cpf != corrida["motorista_cpf"]:
            return jsonify({"erro": "Ação não autorizada."}), 403

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

@app.route("/corridas/emergentes/recuperar_estado", methods=["GET"])
@token_requerido
def recuperar_estado_corrida():
    cpf_usuario = request.usuario_logado["cpf"]
    conexao = conectar_banco()
    cursor = conexao.cursor(cursor_factory=RealDictCursor)
    try:
        # Busca se o usuário tem alguma corrida onde ele é motorista ou passageiro e que não foi finalizada/cancelada
        cursor.execute("""
            SELECT c.*, u.nome as motorista_nome, u.veiculo, u.placa 
            FROM corridas_emergentes c 
            LEFT JOIN usuarios u ON c.motorista_cpf = u.cpf 
            WHERE (c.passageiro_cpf = %s OR c.motorista_cpf = %s) 
            AND c.status IN ('Aceita', 'Em Viagem')
            ORDER BY c.data_criacao DESC LIMIT 1
        """, (cpf_usuario, cpf_usuario))
        corrida = cursor.fetchone()

        if corrida:
            return jsonify({
                "id": corrida["id"],
                "status": corrida["status"],
                "motorista_nome": corrida.get("motorista_nome", ""),
                "veiculo": corrida.get("veiculo", ""),
                "placa": corrida.get("placa", ""),
                "veiculo_tipo": corrida.get("veiculo_tipo", "Carro"),
                "origem_latitude": float(corrida["origem_latitude"]),
                "origem_longitude": float(corrida["origem_longitude"]),
                "destino_latitude": float(corrida["destino_latitude"]),
                "destino_longitude": float(corrida["destino_longitude"]),
                "endereco_origem": corrida.get("endereco_origem", ""),
                "endereco_destino": corrida.get("endereco_destino", ""),
                "is_motorista_desta_corrida": corrida["motorista_cpf"] == cpf_usuario
            }), 200
        else:
            return jsonify({"mensagem": "Nenhuma corrida ativa encontrada."}), 200
    except Exception as e:
        print(f"Erro ao recuperar estado: {e}")
        return jsonify({"erro": "Erro ao recuperar estado"}), 500
    finally:
        cursor.close()
        conexao.close()
        
@app.route("/corridas_emergentes/atualizar_localizacao", methods=["POST"])
@token_requerido
def atualizar_localizacao_motorista():
    motorista_cpf = request.usuario_logado["cpf"]
    dados = request.get_json()
    id_corrida = dados.get("id")
    lat = dados.get("motorista_latitude")
    lng = dados.get("motorista_longitude")

    if not id_corrida or lat is None or lng is None:
        return jsonify({"erro": "Dados incompletos"}), 400

    conexao = conectar_banco()
    cursor = conexao.cursor()
    try:
        cursor.execute("""
            UPDATE corridas_emergentes 
            SET motorista_latitude = %s, motorista_longitude = %s 
            WHERE id = %s AND motorista_cpf = %s
        """, (lat, lng, id_corrida, motorista_cpf))
        conexao.commit()
        return jsonify({"mensagem": "Localização atualizada"}), 200
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
def obtener_historico_emergente_passageiro(cpf):
    conexao = conectar_banco()
    cursor = conexao.cursor(cursor_factory=RealDictCursor)
    try:
        cursor.execute("""
            SELECT id, endereco_origem, endereco_destino, status,
                   to_char(data_criacao AT TIME ZONE 'America/Sao_Paulo', 'DD/MM/YYYY HH24:MI') as data_criacao,
                   to_char(data_finalizacao AT TIME ZONE 'America/Sao_Paulo', 'DD/MM/YYYY HH24:MI') as data_finalizacao
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
def obtener_historico_emergente_motorista(cpf):
    conexao = conectar_banco()
    cursor = conexao.cursor(cursor_factory=RealDictCursor)
    try:
        cursor.execute("""
            SELECT c.id, c.endereco_origem, c.endereco_destino, c.status,
                   to_char(c.data_criacao AT TIME ZONE 'America/Sao_Paulo', 'DD/MM/YYYY HH24:MI') as data_criacao,
                   to_char(c.data_finalizacao AT TIME ZONE 'America/Sao_Paulo', 'DD/MM/YYYY HH24:MI') as data_finalizacao,
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
# 🟢 NOVOS PLUGUES DE PAGAMENTO:
from controllers.pagamento_emergente_controller import configurar_rotas_pagamento_emergente
from controllers.pagamento_programado_controller import configurar_rotas_pagamento_programado

configurar_rotas_solicitacao(app, conectar_banco, enviar_notificacao)
configurar_rotas_carona(app, conectar_banco)
configurar_rotas_usuario(app, conectar_banco, token_requerido, JWT_SECRET)

# 🟢 INICIANDO AS ROTAS DE PAGAMENTO:
configurar_rotas_pagamento_emergente(app, conectar_banco, token_requerido)
configurar_rotas_pagamento_programado(app, conectar_banco, token_requerido)

# 3. Bloco de execução principal no final do arquivo
if __name__ == "__main__":
    # Garante que as tabelas sejam criadas/verificadas ao iniciar o app
    inicializar_banco()
    #porta = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=5000, debug=True)