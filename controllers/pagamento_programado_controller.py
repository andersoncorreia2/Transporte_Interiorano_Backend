from flask import jsonify, request
import uuid
import os
import json
import urllib.request
import urllib.error
import requests  # 🟢 NECESSÁRIO PARA CONSULTAR A API DO MERCADO PAGO


def configurar_rotas_pagamento_programado(app, conectar_banco, token_requerido):

    # 1. Gera a Taxa de Reserva real via Mercado Pago (R$ 5,00) e trava a vaga por 24h
    @app.route("/pagamentos/programado/gerar_taxa", methods=["POST"])
    @token_requerido
    def gerar_taxa_reserva():
        passageiro_cpf = request.usuario_logado["cpf"]
        nome_passageiro = request.usuario_logado.get("nome", "Passageiro")
        email_passageiro = request.usuario_logado.get("email", "passageiro@transporte.com")
        dados = request.get_json()
        carona_id = dados.get("carona_id")
        
        if not carona_id:
            return jsonify({"erro": "ID da carona não informado."}), 400

        TAXA_RESERVA_BRL = 5.00 
        mp_access_token = os.environ.get("MERCADO_PAGO_ACCESS_TOKEN")
        
        if not mp_access_token:
            return jsonify({"erro": "Token do Mercado Pago não configurado no servidor."}), 500

        conexao = conectar_banco()
        if not conexao:
            return jsonify({"erro": "Banco de dados offline"}), 500

        cursor = conexao.cursor()
        try:
            mp_payload = {
                "transaction_amount": TAXA_RESERVA_BRL,
                "description": "Taxa de Reserva - Viagem Programada",
                "payment_method_id": "pix",
                "payer": {
                    "email": email_passageiro,
                    "first_name": nome_passageiro
                }
            }

            req = urllib.request.Request(
                "https://api.mercadopago.com/v1/payments",
                data=json.dumps(mp_payload).encode("utf-8"),
                headers={
                    "Authorization": f"Bearer {mp_access_token}",
                    "Content-Type": "application/json"
                },
                method="POST"
            )

            try:
                with urllib.request.urlopen(req) as response:
                    resposta_mp = json.loads(response.read().decode("utf-8"))
            except urllib.error.HTTPError as e:
                erro_corpo = e.read().decode("utf-8")
                print(f"❌ Erro Mercado Pago Taxa Reserva: {erro_corpo}")
                return jsonify({"erro": "Falha ao gerar Pix real no Mercado Pago.", "detalhe": erro_corpo}), 400

            payment_id = str(resposta_mp.get("id"))
            point_of_interaction = resposta_mp.get("point_of_interaction", {})
            transaction_data = point_of_interaction.get("transaction_data", {})
            
            pix_copia_cola = transaction_data.get("qr_code", "")
            qr_code_base64 = transaction_data.get("qr_code_base64", "")

            # Registra no banco a solicitação como 'Pendente' para que o app aplique 
            # a regra dos 15 minutos para o passageiro pagar a taxa de R$ 5,00 inicial
            cursor.execute("""
                INSERT INTO solicitacoes (carona_id, passageiro, passageiro_cpf, status, taxa_reserva_paga, payment_id_reserva, data_criacao)
                VALUES (%s, %s, %s, 'Pendente', FALSE, %s, CURRENT_TIMESTAMP)
                RETURNING id
            """, (carona_id, nome_passageiro, passageiro_cpf, payment_id))
            
            solicitacao_id = cursor.fetchone()[0]
            conexao.commit()

            return jsonify({
                "mensagem": "Pix de reserva gerado com sucesso!",
                "solicitacao_id": solicitacao_id,
                "valor": TAXA_RESERVA_BRL,
                "pix_copia_cola": pix_copia_cola,
                "qr_code_base64": qr_code_base64,
                "payment_id": payment_id
            }), 200

        except Exception as e:
            conexao.rollback()
            return jsonify({"erro": str(e)}), 500
        finally:
            cursor.close()
            conexao.close()
        
    # 🟢 ROTA PARA VERIFICAR SE O PIX DA TAXA DE R$ 5,00 FOI PAGO E ATIVAR AS 24H PARA O SALDO
    @app.route("/pagamentos/programado/verificar_pagamento_taxa/<int:solicitacao_id>", methods=["GET"])
    @token_requerido
    def verificar_pagamento_taxa(solicitacao_id):
        conexao = conectar_banco()
        if not conexao:
            return jsonify({"erro": "Banco de dados offline"}), 500
        
        cursor = conexao.cursor()
        try:
            cursor.execute("""
                SELECT payment_id_reserva, taxa_reserva_paga, status 
                FROM solicitacoes WHERE id = %s
            """, (solicitacao_id,))
            solicitacao = cursor.fetchone()
            
            if not solicitacao:
                return jsonify({"pago": False, "erro": "Solicitação não encontrada."}), 404
            
            payment_id, taxa_paga, status_atual = solicitacao[0], solicitacao[1], solicitacao[2]
            
            if taxa_paga:
                return jsonify({"pago": True, "status": status_atual}), 200
                
            if not payment_id:
                return jsonify({"pago": False, "mensagem": "Nenhum pagamento associado a esta solicitação."}), 200
                
            # Consulta ativa na API do Mercado Pago para confirmar a aprovação do Pix de R$ 5,00
            access_token = os.getenv("MERCADO_PAGO_ACCESS_TOKEN", "").strip()
            if access_token:
                headers = {"Authorization": f"Bearer {access_token}"}
                try:
                    res = requests.get(f"https://api.mercadopago.com/v1/payments/{payment_id}", headers=headers, timeout=5)
                    if res.status_code == 200:
                        pagamento_info = res.json()
                        if pagamento_info.get("status") == "approved":
                            # SUCESSO! A taxa de R$ 5,00 foi paga. 
                            # Marca como paga e inicia a contagem exata de 24 horas para o saldo restante.
                            cursor.execute("""
                                UPDATE solicitacoes 
                                SET taxa_reserva_paga = TRUE, 
                                    status = 'Taxa Paga - Aguardando Saldo', 
                                    data_limite_pagamento = CURRENT_TIMESTAMP + INTERVAL '24 hours'
                                WHERE id = %s
                            """, (solicitacao_id,))
                            conexao.commit()
                            
                            print(f"🎉 SUCESSO! Taxa de reserva da solicitação #{solicitacao_id} paga. Prazo de 24h iniciado.")
                            return jsonify({"pago": True, "status": "Taxa Paga - Aguardando Saldo"}), 200
                except Exception as ex:
                    print(f"⚠️ Erro ao consultar status da taxa no Mercado Pago: {ex}")
                    
            return jsonify({"pago": False}), 200
        except Exception as e:
            if conexao:
                conexao.rollback()
            return jsonify({"erro": str(e)}), 500
        finally:
            cursor.close()
            conexao.close()

    # 🟢 Rota: Verifica se o passageiro pode solicitar (bloqueia se houver débito)
    @app.route("/pagamentos/programado/verificar_permissao", methods=["GET"])
    @token_requerido
    def verificar_permissao_global():
        cpf = request.usuario_logado["cpf"]
        conexao = conectar_banco()
        cursor = conexao.cursor()
        try:
            # AQUI ESTÁ A REGRA GLOBAL: 
            # Consultamos a tabela de usuários, não a de corridas.
            # Se estiver bloqueado em qualquer lugar, bloqueia aqui também.
            cursor.execute("SELECT bloqueado FROM usuarios WHERE cpf = %s", (cpf,))
            usuario = cursor.fetchone()
            
            if usuario and usuario[0] is True:
                return jsonify({"bloqueado": True, "mensagem": "Acesso suspenso. Regularize seu débito pendente para utilizar nossos serviços."}), 200
                
            return jsonify({"bloqueado": False}), 200
        finally:
            cursor.close()
            conexao.close()