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

def conectar_banco():
    DATABASE_URL = "postgresql://transporte_db_mc40_user:e1JFSWlEYZqmdecHqMUM2ZMxM6h43Zbb@dpg-d893okfavr4c739abl50-a.oregon-postgres.render.com/transporte_db_mc40"
    try:
        conexao = psycopg2.connect(DATABASE_URL)
        return conexao
    except Exception as e:
        print(f"Erro ao conectar no banco: {e}")
        return None

# --- NOVA ROTA: Registrar Token ---
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
        # 1. Tabela de usuários
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
        cursor.execute("ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS fcm_token TEXT;")

        # 2. Tabela de caronas
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
        # E garanta que a coluna exista caso a tabela já tivesse sido criada antes:
        cursor.execute("ALTER TABLE caronas ADD COLUMN IF NOT EXISTS status TEXT DEFAULT 'Aberta';")
        
        # Garantir colunas na caronas
        cursor.execute("ALTER TABLE caronas ADD COLUMN IF NOT EXISTS evento_nome TEXT;")
        cursor.execute("ALTER TABLE caronas ADD COLUMN IF NOT EXISTS cidade_origem TEXT;")
        cursor.execute("ALTER TABLE caronas ADD COLUMN IF NOT EXISTS endereco_origem TEXT;")
        cursor.execute("ALTER TABLE caronas ADD COLUMN IF NOT EXISTS cidade_destino TEXT;")
        cursor.execute("ALTER TABLE caronas ADD COLUMN IF NOT EXISTS endereco_destino TEXT;")
        
        # 3. Tabela de solicitações
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS solicitacoes (
                id SERIAL PRIMARY KEY,
                carona_id INTEGER,
                passageiro TEXT,
                status TEXT
            )
        """)

        cursor.execute("ALTER TABLE solicitacoes ADD COLUMN IF NOT EXISTS data_criacao TIMESTAMP DEFAULT CURRENT_TIMESTAMP;")       
        
        cursor.execute("ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS corridas_realizadas INTEGER DEFAULT 0;")
        cursor.execute("ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS passageiros_conduzidos INTEGER DEFAULT 0;")
        
        conexao.commit()
        print("✅ Tabelas e colunas verificadas com sucesso!")
        
    except Exception as e:
        print(f"❌ Erro ao criar tabelas: {e}")
        conexao.rollback()
    finally:
        cursor.close()
        conexao.close()

criar_tabelas()

@app.route("/usuarios", methods=["POST"])
def cadastrar_usuario():
    dados = request.get_json()
    conexao = conectar_banco()
    cursor = conexao.cursor()
    try:
        cursor.execute("""
            INSERT INTO usuarios (nome, cpf, email, telefone, veiculo, placa, senha, vagas, rua, numero, complemento, bairro, cidade, estado, cep)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """, (
            dados["nome"], dados["cpf"], dados["email"], dados["telefone"],
            dados.get("veiculo", ""), dados.get("placa", ""), dados["senha"], dados.get("vagas", "0"),
            dados.get("rua", ""), dados.get("numero", ""), dados.get("complemento", ""),
            dados.get("bairro", ""), dados.get("cidade", ""), dados.get("estado", ""), dados.get("cep", "")
        ))
        conexao.commit()
        return jsonify({"mensagem": "Usuário guardado!"}), 201
    except IntegrityError:
        conexao.rollback()
        return jsonify({"erro": "Esse CPF ou E-mail já está cadastrado!"}), 400
    finally:
        cursor.close()
        conexao.close()

@app.route("/usuarios/<email_seguro>", methods=["PUT"])
def atualizar_usuario(email_seguro):
    email_real = urllib.parse.unquote(email_seguro)
    dados = request.get_json()
    conexao = conectar_banco()
    cursor = conexao.cursor()
    try:
        cursor.execute("""
            UPDATE usuarios 
            SET nome=%s, telefone=%s, veiculo=%s, placa=%s, vagas=%s,
            rua=%s, numero=%s, complemento=%s, bairro=%s, cidade=%s, estado=%s, cep=%s
            WHERE email=%s
        """, (
            dados["nome"], dados["telefone"], dados.get("veiculo", ""), dados.get("placa", ""), dados.get("vagas", "0"),
            dados.get("rua", ""), dados.get("numero", ""), dados.get("complemento", ""),
            dados.get("bairro", ""), dados.get("cidade", ""), dados.get("estado", ""), dados.get("cep", ""),
            email_real
        ))
        conexao.commit()
        return jsonify({"mensagem": "Dados updated!"}), 200
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
    
    if usuario_encontrado:
        return jsonify({"existe": True}), 200
    else:
        return jsonify({"existe": False}), 200

@app.route("/usuarios_por_email/<email_seguro>", methods=["GET"])
def buscar_por_email(email_seguro):
    email_real = urllib.parse.unquote(email_seguro)
    conexao = conectar_banco()
    cursor = conexao.cursor(cursor_factory=RealDictCursor)
    cursor.execute("SELECT corridas_realizadas, passageiros_conduzidos FROM usuarios WHERE email = %s", (email_real,))
    usuario = cursor.fetchone()
    cursor.close()
    conexao.close()
    
    if usuario:
        return jsonify(usuario), 200
    else:
        return jsonify({"corridas_realizadas": 0, "passageiros_conduzidos": 0}), 200

@app.route("/usuarios_por_nome/<nome_motorista>", methods=["GET"])
def buscar_por_nome(nome_motorista):
    nome_real = urllib.parse.unquote(nome_motorista)
    conexao = conectar_banco()
    cursor = conexao.cursor(cursor_factory=RealDictCursor)
    # Busca métricas pelo NOME (usando TRIM/LOWER para evitar erros de digitação)
    cursor.execute("""
        SELECT corridas_realizadas, passageiros_conduzidos 
        FROM usuarios 
        WHERE TRIM(LOWER(nome)) = TRIM(LOWER(%s))
    """, (nome_real,))
    usuario = cursor.fetchone()
    cursor.close()
    conexao.close()
    
    if usuario:
        return jsonify(usuario), 200
    return jsonify({"corridas_realizadas": 0, "passageiros_conduzidos": 0}), 200

@app.route("/usuarios_por_cpf/<cpf>", methods=["GET"])
def buscar_por_cpf(cpf):
    conexao = conectar_banco()
    cursor = conexao.cursor(cursor_factory=RealDictCursor)
    # Busca métricas pelo CPF do motorista
    cursor.execute("SELECT corridas_realizadas, passageiros_conduzidos FROM usuarios WHERE cpf = %s", (cpf,))
    usuario = cursor.fetchone()
    cursor.close()
    conexao.close()
    
    if usuario:
        return jsonify(usuario), 200
    else:
        return jsonify({"corridas_realizadas": 0, "passageiros_conduzidos": 0}), 200

@app.route("/usuarios/<email_seguro>", methods=["DELETE"])
def excluir_conta(email_seguro):
    email_real = urllib.parse.unquote(email_seguro)
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


@app.route("/login", methods=["POST"])
def login():
    dados = request.get_json()
    conexao = conectar_banco()
    cursor = conexao.cursor(cursor_factory=RealDictCursor)
    cursor.execute("""
        SELECT nome, cpf, email, telefone, veiculo, placa, vagas, 
               rua, numero, complemento, bairro, cidade, estado, cep 
        FROM usuarios 
        WHERE email = %s AND senha = %s
    """, (dados["email"], dados["senha"]))

    usuario = cursor.fetchone()
    cursor.close()
    conexao.close()

    if usuario:
        return jsonify({
            "nome": usuario["nome"], "cpf": usuario["cpf"], "email": usuario["email"],
            "telefone": usuario["telefone"], "veiculo": usuario.get("veiculo", ""),
            "placa": usuario.get("placa", ""), "vagas": usuario.get("vagas", "0"),
            "rua": usuario.get("rua", ""), "numero": usuario.get("numero", ""),
            "complemento": usuario.get("complemento", ""), "bairro": usuario.get("bairro", ""),
            "cidade": usuario.get("cidade", ""), "estado": usuario.get("estado", ""), "cep": usuario.get("cep", "")
        }), 200
    else:
        return jsonify({"erro": "Acesso negado"}), 401


# 🆕 NOVA ROTA: Recuperação de Senha Segura
@app.route("/recuperar_senha", methods=["POST"])
def recuperar_senha():
    dados = request.get_json()
    email = dados.get("email")
    cpf = dados.get("cpf")
    nova_senha = dados.get("senha")

    conexao = conectar_banco()
    cursor = conexao.cursor(cursor_factory=RealDictCursor)

    # 1. Checa se existe um usuário com ESSE email E ESSE cpf juntos
    cursor.execute("SELECT email FROM usuarios WHERE email = %s AND cpf = %s", (email, cpf))
    usuario = cursor.fetchone()

    if usuario:
        # 2. Se tudo bater, troca a senha antiga pela nova!
        cursor.execute("UPDATE usuarios SET senha = %s WHERE email = %s AND cpf = %s", (nova_senha, email, cpf))
        conexao.commit()
        cursor.close()
        conexao.close()
        return jsonify({"mensagem": "Senha alterada com sucesso!"}), 200
    else:
        cursor.close()
        conexao.close()
        return jsonify({"erro": "E-mail ou CPF incorretos!"}), 400


@app.route("/caronas", methods=["GET"])
def listar_caronas():
    # 🆕 AGORA BUSCA APENAS AS CARONAS COM STATUS 'Aberta'
    conexao = conectar_banco()
    cursor = conexao.cursor(cursor_factory=RealDictCursor)
    cursor.execute("SELECT * FROM caronas WHERE status = 'Aberta'") 
    caronas_do_cofre = cursor.fetchall()
    cursor.close()
    conexao.close()

    lista_caronas = []
    for carona in caronas_do_cofre:
        lista_caronas.append({
            "id": carona["id"], 
            "evento_nome": carona.get("evento_nome", ""),
            "cidade_origem": carona.get("cidade_origem", ""),
            "origem": carona.get("endereco_origem", ""),
            "cidade_destino": carona.get("cidade_destino", ""),
            "destino": carona.get("endereco_destino", ""),
            "horario": carona.get("horario", ""),
            "vagas": carona.get("vagas", "0"),
            "motorista": carona.get("motorista", ""),
            "motorista_cpf": carona.get("motorista_cpf", ""),
            "status": carona.get("status", "Aberta")
        })
    return jsonify(lista_caronas)

# --- ROTA POST ATUALIZADA ---
@app.route("/caronas", methods=["POST"])
def criar_carona():
    dados = request.get_json()
    conexao = conectar_banco()
    cursor = conexao.cursor()
    
    # 1. Insere a carona
    cursor.execute("""
        INSERT INTO caronas (evento_nome, cidade_origem, endereco_origem, cidade_destino, endereco_destino, horario, vagas, motorista, motorista_cpf)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
    """, (dados["evento_nome"], dados["cidade_origem"], dados["endereco_origem"], 
          dados["cidade_destino"], dados["endereco_destino"], dados["horario"], 
          dados["vagas"], dados["motorista"], dados["motorista_cpf"]))
    
    # 2. Adicione este bloco de volta para atualizar o total de vagas ofertadas
    cursor.execute("""
        UPDATE usuarios 
        SET vagas_ofertadas = COALESCE(vagas_ofertadas, 0) + %s
        WHERE cpf = %s
    """, (int(dados["vagas"]), dados["motorista_cpf"]))
    
    print(f"DEBUG: Atualizando motorista CPF {dados['motorista_cpf']}. Linhas afetadas: {cursor.rowcount}")
     
    conexao.commit()
    cursor.close()
    conexao.close()
    return jsonify({"mensagem": "Evento criado!"}), 201

@app.route("/caronas/<int:id_carona>", methods=["DELETE"])
def deletar_carona(id_carona):
    conexao = conectar_banco()
    cursor = conexao.cursor()
    cursor.execute("DELETE FROM caronas WHERE id = %s", (id_carona,))
    cursor.execute("DELETE FROM solicitacoes WHERE carona_id = %s", (id_carona,))
    conexao.commit()
    cursor.close()
    conexao.close()
    return jsonify({"mensagem": "Evento e solicitações excluídos!"}), 200

@app.route("/solicitacoes", methods=["GET"])
def listar_solicitacoes():
    conexao = conectar_banco()
    cursor = conexao.cursor(cursor_factory=RealDictCursor)
    
    # 1. A MÁGICA: Apaga os expirados do banco antes de listar!
    cursor.execute("DELETE FROM solicitacoes WHERE status = 'Expirado'")
    conexao.commit()

    # 2. Busca tudo que sobrou
    cursor.execute("SELECT * FROM solicitacoes WHERE status != 'Finalizado'")
    solicitacoes_do_cofre = cursor.fetchall()
    
    lista_solicitacoes = []
    agora = datetime.now()

    for sol in solicitacoes_do_cofre:
        status = sol["status"]
        
        # Lógica de expiração (se for Pendente e passou de 15 min)
        if status == "Pendente" and sol["data_criacao"]:
            if (agora - sol["data_criacao"]) > timedelta(minutes=15):
                status = "Expirado"
                cursor.execute("UPDATE solicitacoes SET status = %s WHERE id = %s", (status, sol["id"]))
                conexao.commit()

        lista_solicitacoes.append({
            "id": sol["id"], 
            "carona_id": sol["carona_id"], 
            "passageiro": sol["passageiro"], 
            "status": status
        })
    
    cursor.close()
    conexao.close()
    return jsonify(lista_solicitacoes), 200

@app.route("/solicitacoes", methods=["POST"])
def pedir_carona():
    dados = request.get_json()
    carona_id = int(dados["carona_id"])
    cpf_passageiro = dados.get("passageiro_cpf") # Captura o CPF enviado pelo Android
    
    conexao = conectar_banco()
    cursor = conexao.cursor(cursor_factory=RealDictCursor)
    
    # 1. Busca a carona E o fcm_token do motorista
    cursor.execute("""
        SELECT c.vagas, u.fcm_token 
        FROM caronas c 
        JOIN usuarios u ON c.motorista = u.nome 
        WHERE c.id = %s
    """, (carona_id,))
    resultado = cursor.fetchone()

    # 2. Se a carona existir e tiver vaga, registra e notifica
    if resultado and int(resultado["vagas"]) > 0:
        cursor.execute("""
            INSERT INTO solicitacoes (carona_id, passageiro, passageiro_cpf, status, data_criacao) 
            VALUES (%s, %s, %s, %s, %s)
        """, (carona_id, dados["passageiro"], cpf_passageiro, "Pendente", datetime.now()))
        
        conexao.commit()
        
        # 3. Dispara a notificação se o motorista tiver um token salvo
        if resultado.get("fcm_token"):
            enviar_notificacao(resultado["fcm_token"], "Nova Solicitação!", f"{dados['passageiro']} quer uma vaga.")
            
        cursor.close()
        conexao.close()
        return jsonify({"mensagem": "Pedido registrado!"}), 201
    
    cursor.close()
    conexao.close()
    return jsonify({"erro": "Carona sem vagas ou inexistente."}), 400

@app.route("/solicitacoes/<int:id_solicitacao>", methods=["PUT"])
def responder_solicitacao(id_solicitacao):
    dados = request.get_json()
    novo_status = dados["status"]

    conexao = conectar_banco()
    cursor = conexao.cursor(cursor_factory=RealDictCursor)

    cursor.execute("UPDATE solicitacoes SET status = %s WHERE id = %s", (novo_status, id_solicitacao))

    conexao.commit()
    cursor.close()
    conexao.close()
    return jsonify({"mensagem": f"Status atualizado para {novo_status}!"}), 200

@app.route("/solicitacoes/<int:id_solicitacao>", methods=["DELETE"])
def cancelar_solicitacao(id_solicitacao):
    conexao = conectar_banco()
    cursor = conexao.cursor()
    
    cursor.execute("DELETE FROM solicitacoes WHERE id = %s", (id_solicitacao,))
    
    conexao.commit()
    cursor.close()
    conexao.close()
    
    return jsonify({"mensagem": "Pedido cancelado pelo passageiro e vaga devolvida!"}), 200

@app.route("/finalizar_solicitacao", methods=["POST"])
def finalizar_solicitacao():
    dados = request.get_json()
    # Espera: {"solicitacao_id": id, "motorista": nome_motorista, "passageiro_cpf": cpf, "carona_id": id}
    conexao = conectar_banco()
    cursor = conexao.cursor(cursor_factory=RealDictCursor)
    
    try:
        # 1. Finaliza a solicitação e busca CPFs para garantir precisão
        cursor.execute("UPDATE solicitacoes SET status = 'Finalizado' WHERE id = %s", (dados["solicitacao_id"],))
        
        # Busca o CPF do motorista e do passageiro para garantir a atualização correta
        cursor.execute("""
            SELECT s.passageiro_cpf, c.motorista_cpf, c.vagas 
            FROM solicitacoes s
            JOIN caronas c ON s.carona_id = c.id
            WHERE s.id = %s
        """, (dados["solicitacao_id"],))
        info = cursor.fetchone()
        
        # 2. O passageiro ganha +1 corrida (PELO CPF)
        cursor.execute("""
            UPDATE usuarios 
            SET corridas_realizadas = COALESCE(corridas_realizadas, 0) + 1
            WHERE cpf = %s
        """, (info['passageiro_cpf'],))
        
        # 3. O motorista ganha +1 passageiro conduzido (PELO CPF DO MOTORISTA)
        cursor.execute("""
            UPDATE usuarios 
            SET passageiros_conduzidos = COALESCE(passageiros_conduzidos, 0) + 1
            WHERE cpf = %s
        """, (info['motorista_cpf'],))

        # 4. Verifica se este foi o ÚLTIMO passageiro do evento
        cursor.execute("""
            SELECT COUNT(*) as pendentes 
            FROM solicitacoes 
            WHERE carona_id = %s AND status != 'Finalizado'
        """, (dados["carona_id"],))
        
        pendentes = cursor.fetchone()["pendentes"]
        
        # 5. VERIFICAÇÃO FINAL (Método Seguro)
        cursor.execute("SELECT count(*) as count FROM solicitacoes WHERE carona_id = %s AND status ILIKE 'Finalizado'", (dados["carona_id"],))
        finalizados = cursor.fetchone()['count']
        
        cursor.execute("SELECT count(*) as count FROM solicitacoes WHERE carona_id = %s", (dados["carona_id"],))
        total = cursor.fetchone()['count']
        
        # 6. Se o total for igual ou maior ao número de finalizados, o evento acabou!
        if total <= finalizados:
            # Atualiza corrida do motorista
            cursor.execute("UPDATE usuarios SET corridas_realizadas = corridas_realizadas + 1 WHERE cpf = %s", (info['motorista_cpf'],))
            # Finaliza o evento
            cursor.execute("UPDATE caronas SET status = 'Finalizado' WHERE id = %s", (dados["carona_id"],))
            
            # ATUALIZAÇÃO DAS VAGAS OFERTADAS (Aqui entra o seu novo requisito)
            cursor.execute("""
                UPDATE usuarios 
                SET vagas_ofertadas = COALESCE(vagas_ofertadas, 0) + %s
                WHERE cpf = %s
            """, (int(info['vagas']), info['motorista_cpf']))
            
            print(f"DEBUG: Evento {dados['carona_id']} finalizado com sucesso.")
        
        conexao.commit()
        return jsonify({"mensagem": "Viagem finalizada!", "total": total, "finalizados": finalizados}), 200
    except Exception as e:
        # Se ocorrer QUALQUER erro (banco, conexão, dados faltando), 
        # o rollback desfaz qualquer alteração parcial para não corromper os dados
        conexao.rollback()
        print(f"❌ Erro na finalização: {e}") # Importante para você ver no log do Render
        return jsonify({"erro": str(e)}), 500
        
    finally:
        # Isso garante que a conexão com o banco seja SEMPRE fechada,
        # evitando que o servidor pare de aceitar novos pedidos por falta de conexões.
        cursor.close()
        conexao.close()
        
@app.route("/historico_cpf/<cpf>", methods=["GET"])
def listar_historico_por_cpf(cpf):
    # O CPF não costuma ter caracteres especiais, mas garantimos a limpeza
    cpf_limpo = urllib.parse.unquote(cpf)
    
    conexao = conectar_banco()
    cursor = conexao.cursor(cursor_factory=RealDictCursor)
    
    # Busca filtrando pelo CPF do passageiro
    cursor.execute("""
        SELECT s.*, c.evento_nome, c.horario, s.passageiro_cpf 
        FROM solicitacoes s 
        JOIN caronas c ON s.carona_id = c.id 
        WHERE s.passageiro_cpf = %s AND s.status = 'Finalizado'
    """, (cpf_limpo,))
    
    historico = cursor.fetchall()
    cursor.close()
    conexao.close()
    
    return jsonify(historico), 200

# Se quiser uma rota para o motorista ver o histórico dele também:
@app.route("/historico_motorista/<motorista>", methods=["GET"])
def listar_historico_motorista(motorista):
    conexao = conectar_banco()
    cursor = conexao.cursor(cursor_factory=RealDictCursor)
    cursor.execute("""
        SELECT s.*, c.evento_nome, c.horario 
        FROM solicitacoes s 
        JOIN caronas c ON s.carona_id = c.id 
        WHERE c.motorista = %s AND s.status = 'Finalizado'
    """, (motorista,))
    historico = cursor.fetchall()
    cursor.close()
    conexao.close()
    return jsonify(historico)      

if __name__ == "__main__":
    print("🚀 Foguete Transporte Interiorano online com Endereços Completos!")
    porta = int(os.environ.get("PORT", 5000))
    app.run(debug=False, host="0.0.0.0", port=porta)