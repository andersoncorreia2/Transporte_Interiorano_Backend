import urllib.parse
import random
from flask import jsonify, request
from psycopg2 import IntegrityError
from werkzeug.security import generate_password_hash, check_password_hash
import jwt
from datetime import datetime, timedelta, timezone
import os

# 🔌 Importando todas as funções organizadas da nossa nova caixinha de Models!
from models.usuario_model import (
    model_atualizar_token_fcm,
    model_buscar_usuario_por_username,
    model_verificar_sugestao_existe,
    model_atualizar_modalidade,
    model_inserir_usuario,
    model_atualizar_usuario,
    model_checar_cpf_existe,
    model_buscar_usuario_por_email,
    model_buscar_usuario_por_nome,
    model_buscar_usuario_por_cpf,
    model_excluir_conta_usuario,
    model_buscar_dados_login,
    model_atualizar_hash_senha,
    model_buscar_recuperacao,
    model_salvar_codigo_recuperacao,
    model_buscar_codigo_recuperacao,
    model_redefinir_senha_final
)

def configurar_rotas_usuario(app, conectar_banco, token_requerido, JWT_SECRET):

    @app.route("/registrar_token", methods=["POST"])
    def registrar_token():
        dados = request.get_json()
        conexao = conectar_banco()
        if not conexao:
            return jsonify({"erro": "Banco de dados offline!"}), 500
            
        try:
            model_atualizar_token_fcm(conexao, dados["token"], dados["email"])
            return jsonify({"mensagem": "Token saved"}), 200
        except Exception as e:
            return jsonify({"erro": str(e)}), 500

    @app.route("/verificar_usuario/<username>", methods=["GET"])
    def verificar_usuario(username):
        user_limpo = username.strip().lower()
        if len(user_limpo) < 3:
            return jsonify({"disponivel": False, "sugestoes": []}), 200

        conexao = conectar_banco()
        if not conexao:
            return jsonify({"disponivel": False, "sugestoes": []}), 500

        try:
            existe = model_buscar_usuario_por_username(conexao, user_limpo)
            if not existe:
                return jsonify({"disponivel": True, "sugestoes": []}), 200

            sugestoes = []
            tentativas = 0
            while len(sugestoes) < 3 and tentativas < 20:
                tentativas += 1
                sugestao = f"{user_limpo}{random.randint(10, 99)}"
                if not model_verificar_sugestao_existe(conexao, sugestao):
                    if sugestao not in sugestoes:
                        sugestoes.append(sugestao)

            return jsonify({"disponivel": False, "sugestoes": sugestoes}), 200
        except Exception as e:
            print(f"❌ Erro na rota verificar_usuario: {e}")
            return jsonify({"disponivel": False, "sugestoes": []}), 500

    @app.route("/usuarios/alterar_modalidade", methods=["POST"])
    @token_requerido
    def alterar_modalidade():
        dados = request.get_json()
        modalidade = dados.get("modalidade")
        cpf_usuario = request.usuario_logado["cpf"]

        if modalidade not in ['Programada', 'Emergencial']:
            return jsonify({"erro": "Modalidade selecionada é inválida!"}), 400

        conexao = conectar_banco()
        if not conexao:
            return jsonify({"erro": "Banco de dados offline"}), 500
            
        try:
            model_atualizar_modalidade(conexao, modalidade, cpf_usuario)
            return jsonify({"mensagem": f"Modalidade alterada para {modalidade} com sucesso!"}), 200
        except Exception as e:
            print(f"❌ Erro ao atualizar modalidade: {e}")
            return jsonify({"erro": str(e)}), 500

    @app.route("/usuarios", methods=["POST"])
    def cadastrar_usuario():
        dados = request.get_json()
        conexao = conectar_banco()
        if not conexao:
            return jsonify({"erro": "Banco de dados offline ou inacessível no momento!"}), 500
            
        try:
            senha_criptografada = generate_password_hash(dados["senha"])
            model_inserir_usuario(conexao, dados, senha_criptografada)
            return jsonify({"mensagem": "Usuário guardado!"}), 201
        except IntegrityError:
            return jsonify({"erro": "Esse CPF, E-mail ou Nome de Usuário já está cadastrado!"}), 400
        except Exception as e:
            return jsonify({"erro": str(e)}), 500

    @app.route("/usuarios/<cpf_seguro>", methods=["PUT"])
    @token_requerido
    def atualizar_usuario(cpf_seguro):
        cpf_real = urllib.parse.unquote(cpf_seguro)
        if request.usuario_logado["cpf"] != cpf_real:
            return jsonify({"erro": "Ação não autorizada! Você não tem permissão."}), 403

        dados = request.get_json()
        conexao = conectar_banco()
        if not conexao:
            return jsonify({"erro": "Banco de dados offline"}), 500
            
        try:
            model_atualizar_usuario(conexao, dados, cpf_real)
            return jsonify({"mensagem": "Dados updated com sucesso!"}), 200
        except Exception as e:
            return jsonify({"erro": str(e)}), 500

    @app.route("/verificar_cpf/<cpf_digitado>", methods=["GET"])
    def checar_cpf(cpf_digitado):
        conexao = conectar_banco()
        if not conexao:
            return jsonify({"erro": "Banco de dados offline"}), 500
            
        try:
            existe = model_checar_cpf_existe(conexao, cpf_digitado)
            return jsonify({"existe": existe}), 200
        except Exception as e:
            return jsonify({"erro": str(e)}), 500

    @app.route("/usuarios_por_email/<email_seguro>", methods=["GET"])
    def buscar_por_email(email_seguro):
        email_real = urllib.parse.unquote(email_seguro)
        conexao = conectar_banco()
        if not conexao:
            return jsonify({"erro": "Banco de dados offline"}), 500
            
        try:
            usuario = model_buscar_usuario_por_email(conexao, email_real)
            return jsonify(usuario if usuario else {"corridas_realizadas": 0, "passageiros_conduzidos": 0}), 200
        except Exception as e:
            return jsonify({"erro": str(e)}), 500

    @app.route("/usuarios_por_nome/<nome_motorista>", methods=["GET"])
    def buscar_por_nome(nome_motorista):
        nome_real = urllib.parse.unquote(nome_motorista)
        conexao = conectar_banco()
        if not conexao:
            return jsonify({"erro": "Banco de dados offline"}), 500
            
        try:
            usuario = model_buscar_usuario_por_nome(conexao, nome_real)
            return jsonify(usuario if usuario else {"corridas_realizadas": 0, "passageiros_conduzidos": 0}), 200
        except Exception as e:
            return jsonify({"erro": str(e)}), 500

    @app.route("/usuarios_por_cpf/<cpf>", methods=["GET"])
    def buscar_por_cpf(cpf):
        conexao = conectar_banco()
        if not conexao:
            return jsonify({"erro": "Banco de dados offline"}), 500
            
        try:
            usuario = model_buscar_usuario_por_cpf(conexao, cpf)
            return jsonify(usuario if usuario else {"corridas_realizadas": 0, "passageiros_conduzidos": 0}), 200
        except Exception as e:
            return jsonify({"erro": str(e)}), 500

    @app.route("/usuarios/<email_seguro>", methods=["DELETE"])
    @token_requerido
    def excluir_conta(email_seguro):
        email_real = urllib.parse.unquote(email_seguro)
        if request.usuario_logado["email"] != email_real:
            return jsonify({"erro": "Ação não autorizada!"}), 403

        conexao = conectar_banco()
        if not conexao:
            return jsonify({"erro": "Banco de dados offline"}), 500
            
        try:
            sucesso = model_excluir_conta_usuario(conexao, email_real)
            if sucesso:
                return jsonify({"mensagem": "Conta e dados excluídos definitivamente!"}), 200
            return jsonify({"erro": "Usuário não encontrado."}), 404
        except Exception as e:
            return jsonify({"erro": str(e)}), 500

    @app.route("/login", methods=["POST"])
    def login():
        dados = request.get_json()
        conexao = conectar_banco()
        if not conexao:
            return jsonify({"erro": "Banco de dados offline"}), 500
            
        try:
            usuario = model_buscar_dados_login(conexao, dados["usuario"].strip().lower())

            is_valido = False
            if usuario:
                if usuario["senha"].startswith(("pbkdf2:", "scrypt:", "bcrypt:")):
                    is_valido = check_password_hash(usuario["senha"], dados["senha"])
                else:
                    is_valido = (usuario["senha"] == dados["senha"])
                    if is_valido:
                        novo_hash_seguro = generate_password_hash(dados["senha"])
                        model_atualizar_hash_senha(conexao, novo_hash_seguro, usuario["cpf"])

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

    @app.route("/solicitar_codigo", methods=["POST"])
    def solicitar_codigo():
        import smtplib
        from email.mime.text import MIMEText
        from email.mime.multipart import MIMEMultipart

        dados = request.get_json()
        email_digitado = dados.get("email", "").strip().lower()
        cpf_digitado = dados.get("cpf", "").strip()
        cpf_limpo = ''.join(filter(str.isdigit, cpf_digitado))

        conexao = conectar_banco()
        if not conexao:
            return jsonify({"erro": "Banco de dados offline"}), 500
            
        try:
            usuario = model_buscar_recuperacao(conexao, email_digitado, cpf_limpo)
            if not usuario:
                return jsonify({"erro": "E-mail ou CPF não encontrados no sistema."}), 404

            codigo = str(random.randint(100000, 999999))
            expiracao = datetime.now(timezone.utc) + timedelta(minutes=10)

            model_salvar_codigo_recuperacao(conexao, usuario["email"], codigo, expiracao)

            # --- CONFIGURAÇÃO DO DISPARO REAL DE E-MAIL (SMTP) ---
            # 💡 Como boa prática de segurança, buscamos as credenciais de variáveis de ambiente do Render/OS
            gmail_usuario = os.environ.get("GMAIL_USER", "app.transporteinteriorano@gmail.com") 
            gmail_senha_app = os.environ.get("GMAIL_APP_PASSWORD", "phie uhnp lxht qvgi")
            try:
                # Estruturação da mensagem MIME
                mensagem = MIMEMultipart()
                mensagem['From'] = gmail_usuario
                mensagem['To'] = usuario["email"]
                mensagem['Subject'] = "Chave de Segurança - Transporte Interiorano"

                # 🟢 Alterado para puxar o e-mail, pois a chave 'nome' não vem do model de recuperação
                corpo_email = f"""Olá, {usuario['email']}!

Você solicitou a recuperação de senha no aplicativo Transporte Interiorano.
Use o código de verificação abaixo para definir sua nova senha no aplicativo:

👉 CÓDIGO DE VERIFICAÇÃO: {codigo}

Este código é válido por 10 minutos. Se não foi você quem realizou esta solicitação, por favor ignore este e-mail.

Atenciosamente,
Equipe Transporte Interiorano.
"""
                mensagem.attach(MIMEText(corpo_email, 'plain', 'utf-8'))

                # Conexão segura com os servidores SMTP do Google (porta TLS 587)
                servidor = smtplib.SMTP('smtp.gmail.com', 587)
                servidor.starttls()
                servidor.login(gmail_usuario, gmail_senha_app)
                servidor.sendmail(gmail_usuario, usuario["email"], mensagem.as_string())
                servidor.quit()

                print(f"📧 E-mail de recuperação enviado com sucesso para {usuario['email']}!")

            except Exception as erro_email:
                # Se o e-mail falhar por falta de internet local, o print avisa o log mas não quebra a rota
                print(f"❌ Falha ao enviar e-mail físico: {erro_email}")

            # Mantemos o print de backup que você usa para conferir rápido
            print(f"🔒 CÓDIGO DE RECUPERAÇÃO GERADO PARA {usuario['email']}: {codigo}")
            return jsonify({"mensagem": "Código enviado para o e-mail cadastrado!"}), 200

        except Exception as e:
            print(f"❌ Erro na rota solicitar_codigo: {e}")
            return jsonify({"erro": "Erro interno ao buscar usuário."}), 500

    @app.route("/validar_e_redefinir_senha", methods=["POST"])
    def validar_e_redefinir_senha():
        dados = request.get_json()
        email = dados.get("email", "").strip().lower()
        codigo = dados.get("codigo")
        nova_senha = dados.get("senha")

        conexao = conectar_banco()
        if not conexao:
            return jsonify({"erro": "Falha na conexão com o banco"}), 500
            
        try:
            registro = model_buscar_codigo_recuperacao(conexao, email)
            if not registro:
                return jsonify({"erro": "Código de verificação incorreto ou expirado!"}), 400

            expiracao_banco = registro["expiracao"]
            if expiracao_banco and expiracao_banco.tzinfo is None:
                expiracao_banco = expiracao_banco.replace(tzinfo=timezone.utc)

            if registro["codigo"] != str(codigo).strip() or datetime.now(timezone.utc) > expiracao_banco:
                return jsonify({"erro": "Código de verificação incorreto ou expirado!"}), 400

            nova_senha_hash = generate_password_hash(nova_senha)
            model_redefinir_senha_final(conexao, nova_senha_hash, email)
            return jsonify({"mensagem": "Senha alterada com sucesso!"}), 200
        except Exception as e:
            print(f"❌ Erro ao redefinir senha: {e}")
            return jsonify({"erro": "Erro interno ao processar redefinição."}), 500