from flask import jsonify, request
import uuid
import os
import requests
from datetime import datetime, timedelta, timezone 

def configurar_rotas_pagamento_programado(app, conectar_banco, token_requerido, enviar_notificacao):

    # =================================================================
    # 1. GERA A TAXA DE RESERVA (R$ 5,00) VIA PIX MERCADO PAGO
    # =================================================================
    @app.route("/pagamentos/programado/gerar_taxa", methods=["POST"])
    @token_requerido
    def gerar_taxa_reserva():
        passageiro_cpf = request.usuario_logado["cpf"]
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
            # 🟢 CORREÇÃO 1: Limpa pontuações do CPF para garantir correspondência exata
            cursor.execute("""
                SELECT nome FROM usuarios 
                WHERE REPLACE(REPLACE(cpf, '.', ''), '-', '') = REPLACE(REPLACE(%s, '.', ''), '-', '')
            """, (passageiro_cpf,))
            usuario_db = cursor.fetchone()
            
            nome_passageiro = "Passageiro"
            if usuario_db:
                nome_passageiro = usuario_db[0]
            elif request.usuario_logado.get("nome"):
                nome_passageiro = request.usuario_logado.get("nome")

            # 🟢 CORREÇÃO 2: Forçando a Hora do Brasil e gerando expiração de 30 minutos
            agora_br = datetime.now(timezone(timedelta(hours=-3)))
            data_criacao_brasil = agora_br.strftime('%Y-%m-%d %H:%M:%S') 
            expiracao_pix = agora_br + timedelta(minutes=30)
            data_expiracao_iso = expiracao_pix.strftime("%Y-%m-%dT%H:%M:%S.000-03:00")

            mp_payload = {
                "transaction_amount": TAXA_RESERVA_BRL,
                "description": "Taxa de Reserva - Viagem Programada",
                "payment_method_id": "pix",
                "date_of_expiration": data_expiracao_iso, 
                "payer": {
                    "email": email_passageiro,
                    "first_name": nome_passageiro
                }
            }

            headers = {
                "Authorization": f"Bearer {mp_access_token}",
                "Content-Type": "application/json",
                "X-Idempotency-Key": str(uuid.uuid4()) # 🟢 TRAVA DE SEGURANÇA ADICIONADA
            }

            # 🟢 CORREÇÃO 3: Usando biblioteca requests mais estável
            resposta = requests.post("https://api.mercadopago.com/v1/payments", json=mp_payload, headers=headers, timeout=10)
            
            if resposta.status_code != 201:
                return jsonify({"erro": "Falha ao gerar Pix.", "detalhe": resposta.text}), 400

            resposta_mp = resposta.json()
            payment_id = str(resposta_mp.get("id"))
            transaction_data = resposta_mp.get("point_of_interaction", {}).get("transaction_data", {})
            
            pix_copia_cola = transaction_data.get("qr_code", "")
            qr_code_base64 = transaction_data.get("qr_code_base64", "")

            # 🟢 Inserindo com data de criação explícita (Horário de Brasília)
            cursor.execute("""
                INSERT INTO solicitacoes (carona_id, passageiro, passageiro_cpf, status, taxa_reserva_paga, payment_id_reserva, data_criacao)
                VALUES (%s, %s, %s, 'Pendente', FALSE, %s, %s)
                RETURNING id
            """, (carona_id, nome_passageiro, passageiro_cpf, payment_id, data_criacao_brasil))
            
            solicitacao_id = cursor.fetchone()[0]
            conexao.commit()

            return jsonify({
                "mensagem": "Pix de reserva gerado com sucesso!",
                "solicitacao_id": solicitacao_id,
                "valor": TAXA_RESERVA_BRL,
                "pix_copia_cola": pix_copia_cola,
                "qr_code_base64": qr_code_base64,
                "payment_id": payment_id
            }), 201

        except Exception as e:
            conexao.rollback()
            return jsonify({"erro": str(e)}), 500
        finally:
            cursor.close()
            conexao.close()
        
    # =================================================================
    # 2. VERIFICA SE A TAXA FOI PAGA E INICIA CONTADOR DE 24H
    # =================================================================
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
                
            access_token = os.getenv("MERCADO_PAGO_ACCESS_TOKEN", "").strip()
            if access_token:
                headers = {"Authorization": f"Bearer {access_token}"}
                try:
                    res = requests.get(f"https://api.mercadopago.com/v1/payments/{payment_id}", headers=headers, timeout=5)
                    if res.status_code == 200:
                        pagamento_info = res.json()
                        if pagamento_info.get("status") == "approved":
                            
                            # 🟢 Calcula limite em Brasília pelo Python
                            agora_br = datetime.now(timezone(timedelta(hours=-3)))
                            limite_pagamento = agora_br + timedelta(hours=24)
                            data_limite_brasil = limite_pagamento.strftime('%Y-%m-%d %H:%M:%S')

                            cursor.execute("""
                                UPDATE solicitacoes 
                                SET taxa_reserva_paga = TRUE, 
                                    status = 'Taxa Paga - Aguardando Saldo', 
                                    data_limite_pagamento = %s
                                WHERE id = %s
                            """, (data_limite_brasil, solicitacao_id))                            
                            
                            # 🟢 BUSCA O TOKEN DO MOTORISTA PARA NOTIFICÁ-LO
                            cursor.execute("""
                                SELECT u.fcm_token, c.cidade_destino, s.passageiro
                                FROM solicitacoes s
                                JOIN caronas c ON s.carona_id = c.id
                                JOIN usuarios u ON c.motorista_cpf = u.cpf
                                WHERE s.id = %s
                            """, (solicitacao_id,))
                            dados_notificacao = cursor.fetchone()
                            
                            conexao.commit()
                            
                            # 🟢 DISPARA A NOTIFICAÇÃO PUSH
                            if dados_notificacao and dados_notificacao[0]:
                                token_motorista = dados_notificacao[0]
                                destino = dados_notificacao[1]
                                nome_pass = dados_notificacao[2]
                                enviar_notificacao(
                                    token_motorista, 
                                    "🚗 Nova Reserva de Vaga!", 
                                    f"{nome_pass} pagou a Taxa de Reserva para {destino}. Vaga garantida por 24h!"
                                )
                            
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

    # =================================================================
    # 3. GERA O PIX DO VALOR INTEGRAL DIRETAMENTE
    # =================================================================
    @app.route("/pagamentos/programado/gerar_checkout", methods=["POST"])
    @token_requerido
    def gerar_checkout_integral():
        passageiro_cpf = request.usuario_logado["cpf"]
        email_passageiro = request.usuario_logado.get("email", "passageiro@transporte.com")
        
        dados = request.get_json()
        carona_id = dados.get("carona_id")
        valor_total = dados.get("valor_total")
        
        if not carona_id or valor_total is None:
            return jsonify({"erro": "ID da carona ou valor total não informados."}), 400

        mp_access_token = os.environ.get("MERCADO_PAGO_ACCESS_TOKEN")
        if not mp_access_token:
            return jsonify({"erro": "Token do Mercado Pago não configurado no servidor."}), 500

        conexao = conectar_banco()
        if not conexao:
            return jsonify({"erro": "Banco de dados offline"}), 500

        cursor = conexao.cursor()
        try:
            # 🟢 Busca o nome real do banco
            cursor.execute("""
                SELECT nome FROM usuarios 
                WHERE REPLACE(REPLACE(cpf, '.', ''), '-', '') = REPLACE(REPLACE(%s, '.', ''), '-', '')
            """, (passageiro_cpf,))
            usuario_db = cursor.fetchone()
            
            nome_passageiro = "Passageiro"
            if usuario_db:
                nome_passageiro = usuario_db[0]
            elif request.usuario_logado.get("nome"):
                nome_passageiro = request.usuario_logado.get("nome")

            # 🟢 Forçando a Hora do Brasil e expiração de 30 min
            agora_br = datetime.now(timezone(timedelta(hours=-3)))
            data_criacao_brasil = agora_br.strftime('%Y-%m-%d %H:%M:%S')
            expiracao_pix = agora_br + timedelta(minutes=30)
            data_expiracao_iso = expiracao_pix.strftime("%Y-%m-%dT%H:%M:%S.000-03:00")

            # 🟢 Pagamento direto em vez de Preferences (Para gerar Pix nativo no App)
            mp_payload = {
                "transaction_amount": float(valor_total),
                "description": f"Viagem Programada Integral #{carona_id}",
                "payment_method_id": "pix",
                "date_of_expiration": data_expiracao_iso,
                "payer": {
                    "email": email_passageiro,
                    "first_name": nome_passageiro
                }
            }

            headers = {
                "Authorization": f"Bearer {mp_access_token}",
                "Content-Type": "application/json",
                "X-Idempotency-Key": str(uuid.uuid4())
            }

            resposta = requests.post("https://api.mercadopago.com/v1/payments", json=mp_payload, headers=headers, timeout=10)
            
            if resposta.status_code != 201:
                return jsonify({"erro": "Falha ao gerar Pix integral.", "detalhe": resposta.text}), 400

            resposta_mp = resposta.json()
            payment_id = str(resposta_mp.get("id"))
            transaction_data = resposta_mp.get("point_of_interaction", {}).get("transaction_data", {})
            
            # 🟢 Extrai os dados reais do Pix
            pix_copia_cola = transaction_data.get("qr_code", "")
            qr_code_base64 = transaction_data.get("qr_code_base64", "")

            cursor.execute("""
                INSERT INTO solicitacoes (carona_id, passageiro, passageiro_cpf, status, payment_id_reserva, data_criacao)
                VALUES (%s, %s, %s, 'Pendente', %s, %s)
                RETURNING id
            """, (carona_id, nome_passageiro, passageiro_cpf, payment_id, data_criacao_brasil))
            
            solicitacao_id = cursor.fetchone()[0]
            conexao.commit()

            return jsonify({
                "mensagem": "Pix integral gerado com sucesso!",
                "solicitacao_id": solicitacao_id,
                "pix_copia_cola": pix_copia_cola,
                "qr_code_base64": qr_code_base64,
                "payment_id": payment_id
            }), 201

        except Exception as e:
            conexao.rollback()
            return jsonify({"erro": str(e)}), 500
        finally:
            cursor.close()
            conexao.close()

    # =================================================================
    # 4. VERIFICA SE O VALOR INTEGRAL FOI PAGO E NOTIFICA O MOTORISTA
    # =================================================================
    @app.route("/pagamentos/programado/verificar_pagamento_integral/<int:solicitacao_id>", methods=["GET"])
    @token_requerido
    def verificar_pagamento_integral(solicitacao_id):
        conexao = conectar_banco()
        if not conexao:
            return jsonify({"erro": "Banco de dados offline"}), 500
        
        cursor = conexao.cursor()
        try:
            cursor.execute("""
                SELECT payment_id_reserva, status 
                FROM solicitacoes WHERE id = %s
            """, (solicitacao_id,))
            solicitacao = cursor.fetchone()
            
            if not solicitacao:
                return jsonify({"pago": False, "erro": "Solicitação não encontrada."}), 404
            
            payment_id, status_atual = solicitacao[0], solicitacao[1]
            
            if status_atual == "Aceito":
                return jsonify({"pago": True, "status": status_atual}), 200
                
            if not payment_id:
                return jsonify({"pago": False, "mensagem": "Nenhum pagamento associado a esta solicitação."}), 200
                
            access_token = os.getenv("MERCADO_PAGO_ACCESS_TOKEN", "").strip()
            if access_token:
                headers = {"Authorization": f"Bearer {access_token}"}
                try:
                    res = requests.get(f"https://api.mercadopago.com/v1/payments/{payment_id}", headers=headers, timeout=5)
                    if res.status_code == 200:
                        pagamento_info = res.json()
                        if pagamento_info.get("status") == "approved":
                            
                            cursor.execute("""
                                UPDATE solicitacoes 
                                SET status = 'Aceito' 
                                WHERE id = %s
                            """, (solicitacao_id,))
                            
                            # 🟢 BUSCA O TOKEN DO MOTORISTA PARA NOTIFICÁ-LO DO PAGAMENTO TOTAL
                            cursor.execute("""
                                SELECT u.fcm_token, c.cidade_destino, s.passageiro
                                FROM solicitacoes s
                                JOIN caronas c ON s.carona_id = c.id
                                JOIN usuarios u ON c.motorista_cpf = u.cpf
                                WHERE s.id = %s
                            """, (solicitacao_id,))
                            dados_notificacao = cursor.fetchone()
                            
                            conexao.commit()
                            
                            # 🟢 DISPARA A NOTIFICAÇÃO PUSH
                            if dados_notificacao and dados_notificacao[0]:
                                token_motorista = dados_notificacao[0]
                                destino = dados_notificacao[1]
                                nome_pass = dados_notificacao[2]
                                enviar_notificacao(
                                    token_motorista, 
                                    "✅ Pagamento Integral Confirmado!", 
                                    f"O passageiro {nome_pass} pagou o valor total da viagem para {destino}. Vaga confirmada 100%!"
                                )
                            
                            return jsonify({"pago": True, "status": "Aceito"}), 200
                except Exception as ex:
                    print(f"⚠️ Erro ao consultar status integral no Mercado Pago: {ex}")
                    
            return jsonify({"pago": False}), 200
        except Exception as e:
            if conexao:
                conexao.rollback()
            return jsonify({"erro": str(e)}), 500
        finally:
            cursor.close()
            conexao.close()
    
    # =================================================================
    # 5. GERA O PIX DO SALDO RESTANTE E ADICIONA A COLUNA NO BD
    # =================================================================
    @app.route("/pagamentos/programado/gerar_saldo", methods=["POST"])
    @token_requerido
    def gerar_saldo_restante():
        passageiro_cpf = request.usuario_logado["cpf"]
        email_passageiro = request.usuario_logado.get("email", "passageiro@transporte.com")
        
        dados = request.get_json()
        carona_id = dados.get("carona_id")
        solicitacao_id = dados.get("solicitacao_id")
        valor_saldo = dados.get("valor_saldo")
        
        if not carona_id or not solicitacao_id or valor_saldo is None:
            return jsonify({"erro": "Parâmetros incompletos."}), 400

        mp_access_token = os.environ.get("MERCADO_PAGO_ACCESS_TOKEN")
        conexao = conectar_banco()
        cursor = conexao.cursor()
        try:
            # Garante que a coluna do saldo existe sem precisar resetar o banco
            try:
                cursor.execute("ALTER TABLE solicitacoes ADD COLUMN IF NOT EXISTS payment_id_saldo VARCHAR(100);")
                conexao.commit()
            except Exception:
                conexao.rollback()

            cursor.execute("""
                SELECT nome FROM usuarios 
                WHERE REPLACE(REPLACE(cpf, '.', ''), '-', '') = REPLACE(REPLACE(%s, '.', ''), '-', '')
            """, (passageiro_cpf,))
            usuario_db = cursor.fetchone()
            nome_passageiro = usuario_db[0] if usuario_db else "Passageiro"

            agora_br = datetime.now(timezone(timedelta(hours=-3)))
            expiracao_pix = agora_br + timedelta(minutes=30)
            data_expiracao_iso = expiracao_pix.strftime("%Y-%m-%dT%H:%M:%S.000-03:00")

            mp_payload = {
                "transaction_amount": float(valor_saldo),
                "description": f"Pagamento de Saldo Restante - Viagem #{carona_id}",
                "payment_method_id": "pix",
                "date_of_expiration": data_expiracao_iso,
                "payer": {"email": email_passageiro, "first_name": nome_passageiro}
            }
            headers = {
                "Authorization": f"Bearer {mp_access_token}",
                "Content-Type": "application/json",
                "X-Idempotency-Key": str(uuid.uuid4())
            }

            resposta = requests.post("https://api.mercadopago.com/v1/payments", json=mp_payload, headers=headers, timeout=10)
            if resposta.status_code != 201:
                return jsonify({"erro": "Falha ao gerar Pix.", "detalhe": resposta.text}), 400

            resposta_mp = resposta.json()
            payment_id = str(resposta_mp.get("id"))
            pix_copia_cola = resposta_mp.get("point_of_interaction", {}).get("transaction_data", {}).get("qr_code", "")

            cursor.execute("""
                UPDATE solicitacoes 
                SET payment_id_saldo = %s 
                WHERE id = %s AND passageiro_cpf = %s
            """, (payment_id, solicitacao_id, passageiro_cpf))
            conexao.commit()

            return jsonify({"mensagem": "Pix do saldo gerado!", "pix_copia_cola": pix_copia_cola}), 201

        except Exception as e:
            conexao.rollback()
            return jsonify({"erro": str(e)}), 500
        finally:
            cursor.close()
            conexao.close()

    # =================================================================
    # 6. VERIFICA O SALDO E CONFIRMA A VAGA PARA O MOTORISTA
    # =================================================================
    @app.route("/pagamentos/programado/verificar_pagamento_saldo/<int:solicitacao_id>", methods=["GET"])
    @token_requerido
    def verificar_pagamento_saldo(solicitacao_id):
        conexao = conectar_banco()
        cursor = conexao.cursor()
        try:
            cursor.execute("SELECT payment_id_saldo, status FROM solicitacoes WHERE id = %s", (solicitacao_id,))
            solicitacao = cursor.fetchone()
            
            if not solicitacao:
                return jsonify({"pago": False}), 404
                
            payment_id, status_atual = solicitacao[0], solicitacao[1]
            if status_atual == "Aceito":
                return jsonify({"pago": True}), 200
            if not payment_id:
                return jsonify({"pago": False}), 200

            access_token = os.getenv("MERCADO_PAGO_ACCESS_TOKEN", "").strip()
            headers = {"Authorization": f"Bearer {access_token}"}
            res = requests.get(f"https://api.mercadopago.com/v1/payments/{payment_id}", headers=headers, timeout=5)
            
            if res.status_code == 200 and res.json().get("status") == "approved":
                cursor.execute("UPDATE solicitacoes SET status = 'Aceito' WHERE id = %s", (solicitacao_id,))
                
                cursor.execute("""
                    SELECT u.fcm_token, c.cidade_destino, s.passageiro
                    FROM solicitacoes s
                    JOIN caronas c ON s.carona_id = c.id
                    JOIN usuarios u ON c.motorista_cpf = u.cpf
                    WHERE s.id = %s
                """, (solicitacao_id,))
                dados_not = cursor.fetchone()
                conexao.commit()
                
                if dados_not and dados_not[0]:
                    enviar_notificacao(dados_not[0], "✅ Saldo Quitado!", f"{dados_not[2]} quitou o saldo restante para {dados_not[1]}. Vaga 100% garantida!")
                
                return jsonify({"pago": True}), 200

            return jsonify({"pago": False}), 200
        except Exception as e:
            if conexao: conexao.rollback()
            return jsonify({"erro": str(e)}), 500
        finally:
            cursor.close()
            conexao.close()

    # =================================================================
    # 7. VERIFICA SE O USUÁRIO ESTÁ BLOQUEADO POR INADIMPLÊNCIA
    # =================================================================
    @app.route("/pagamentos/programado/verificar_permissao", methods=["GET"])
    @token_requerido
    def verificar_permissao_global():
        cpf = request.usuario_logado["cpf"]
        conexao = conectar_banco()
        cursor = conexao.cursor()
        try:
            cursor.execute("SELECT bloqueado FROM usuarios WHERE cpf = %s", (cpf,))
            usuario = cursor.fetchone()
            
            if usuario and usuario[0] is True:
                return jsonify({"bloqueado": True, "mensagem": "Acesso suspenso. Regularize seu débito pendente para utilizar nossos serviços."}), 200
                
            return jsonify({"bloqueado": False}), 200
        finally:
            cursor.close()
            conexao.close()