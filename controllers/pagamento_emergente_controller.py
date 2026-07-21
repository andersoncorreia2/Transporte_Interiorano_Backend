# controllers/pagamento_emergente_controller.py
import os
import requests
from flask import jsonify, request
import traceback
from datetime import datetime, timedelta, timezone  # <--- 1. Certifique-se de importar datetime, timedelta e timezone no topo

def configurar_rotas_pagamento_emergente(app, conectar_banco, token_requerido):

    # 1. Trava do Calote (Passageiro)
    @app.route("/pagamentos/emergente/verificar_debito", methods=["GET"])
    @token_requerido
    def verificar_debito_passageiro():
        conexao = None
        cursor = None
        try:
            usuario_logado = getattr(request, "usuario_logado", {}) or {}
            passageiro_cpf = usuario_logado.get("cpf") or usuario_logado.get("usuario")

            if not passageiro_cpf:
                return jsonify({"erro": "CPF/Usuário não identificado no token."}), 400

            conexao = conectar_banco()
            cursor = conexao.cursor()

            cursor.execute("SELECT bloqueado FROM usuarios WHERE cpf = %s", (passageiro_cpf,))
            usuario = cursor.fetchone()
            
            cursor.execute("""
                SELECT id, valor_corrida, endereco_origem, endereco_destino 
                FROM corridas_emergentes 
                WHERE passageiro_cpf = %s AND pago = FALSE AND status = 'Finalizada'
                ORDER BY data_criacao DESC LIMIT 1
            """, (passageiro_cpf,))
            corrida_devedora = cursor.fetchone()
            
            if (usuario and usuario[0] is True) or corrida_devedora is not None:
                valor_debito = float(corrida_devedora[1]) if (corrida_devedora and corrida_devedora[1] is not None) else 0.0
                return jsonify({
                    "bloqueado": True,
                    "mensagem": "Você possui um débito pendente.",
                    "detalhes": {
                        "corrida_id": corrida_devedora[0] if corrida_devedora else None,
                        "valor": valor_debito,
                        "origem": corrida_devedora[2] if corrida_devedora else "",
                        "destino": corrida_devedora[3] if corrida_devedora else ""
                    }
                }), 200
                
            return jsonify({"bloqueado": False, "mensagem": "Nenhum débito encontrado."}), 200
        except Exception as e:
            if conexao:
                conexao.rollback()
            return jsonify({"erro": str(e)}), 500
        finally:
            if cursor:
                cursor.close()
            if conexao:
                conexao.close()

    # 🟢 2. GERAÇÃO DE PIX REAL (R$ 0,01 PARA TESTE) VIA MERCADO PAGO
    @app.route("/pagamentos/emergente/gerar_pix_debito", methods=["POST"])
    @token_requerido
    def gerar_pix_debito():
        print("🟢 ENTROU NA ROTA DE GERAR PIX!")
        conexao = None
        cursor = None
        try:
            usuario_logado = getattr(request, "usuario_logado", {}) or {}
            passageiro_cpf = usuario_logado.get("cpf") or usuario_logado.get("usuario")

            if not passageiro_cpf:
                return jsonify({"erro": "CPF não identificado no token de sessão."}), 400

            dados_req = request.get_json(silent=True) or {}
            corrida_id = dados_req.get("corrida_id")

            conexao = conectar_banco()
            cursor = conexao.cursor()

            # Busca o valor pendente na tabela unificada correta
            cursor.execute("""
                SELECT valor_pendente FROM debitos_passageiros 
                WHERE passageiro_cpf = %s AND status = 'pendente'
            """, (passageiro_cpf,))
            resultado = cursor.fetchone()

            valor_cobrado = float(resultado[0]) if (resultado and resultado[0] is not None) else 0.01

            access_token = os.getenv("MERCADO_PAGO_ACCESS_TOKEN", "").strip()
            if not access_token:
                return jsonify({"erro": "Token do Mercado Pago não configurado no servidor."}), 500

            url_mp = "https://api.mercadopago.com/v1/payments"
            headers = {
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json",
                "X-Idempotency-Key": f"pix-debito-{passageiro_cpf}-{corrida_id or 'geral'}"
            }

            # Definindo a expiração correta para daqui a 30 minutos (evita o erro 4049)
            data_expiracao = (datetime.now(timezone.utc) + timedelta(minutes=30)).strftime('%Y-%m-%dT%H:%M:%SZ')

            payload_mp = {
                "transaction_amount": round(valor_cobrado, 2),
                "description": f"Quitacao de Debito - Corrida #{corrida_id or 'Geral'}",
                "payment_method_id": "pix",
                "date_of_expiration": data_expiracao,  # <--- 2. ADICIONADO AQUI PARA CORRIGIR O ERRO 4049
                "payer": {
                    "email": "comprador.teste.transporte@gmail.com",
                    "first_name": "Passageiro",
                    "last_name": "Teste",
                    "identification": {
                        "type": "CPF",
                        "number": "00000000191"
                    }
                }
            }

            resposta_mp = requests.post(url_mp, json=payload_mp, headers=headers, timeout=15)
            
            if resposta_mp.status_code not in (200, 201):
                print(f"❌ ERRO RETORNADO PELO MERCADO PAGO: {resposta_mp.text}")
                return jsonify({
                    "erro": "Falha na comunicação com o Mercado Pago.",
                    "detalhe_tecnico": resposta_mp.text
                }), 400

            dados_mp = resposta_mp.json()
            ponto_interacao = dados_mp.get("point_of_interaction", {})
            transacao_dados = ponto_interacao.get("transaction_data", {})
            
            pix_copia_cola = transacao_dados.get("qr_code")
            qr_code_base64 = transacao_dados.get("qr_code_base64")
            payment_id = str(dados_mp.get("id"))

            if not pix_copia_cola:
                return jsonify({"erro": "O Mercado Pago não retornou o código Pix Copia e Cola."}), 400

            # Salva o payment_id ou insere caso não exista registro prévio
            cursor.execute("""
                INSERT INTO debitos_passageiros (passageiro_cpf, corrida_id, valor_cobrado, payment_id, status)
                VALUES (%s, %s, %s, %s, 'pendente')
                ON CONFLICT DO NOTHING;
            """, (passageiro_cpf, corrida_id, valor_cobrado, payment_id))

            cursor.execute("""
                UPDATE debitos_passageiros 
                SET payment_id = %s 
                WHERE passageiro_cpf = %s AND status = 'pendente'
            """, (payment_id, passageiro_cpf))
            
            conexao.commit()

            return jsonify({
                "sucesso": True,
                "pix_copia_cola": pix_copia_cola,
                "qr_code_base64": qr_code_base64,
                "valor_cobrado": valor_cobrado,
                "payment_id": payment_id
            }), 200

        except Exception as e:
            if conexao:
                conexao.rollback()
            print(f"🔴 ERRO DETALHADO DA EXCEÇÃO: {repr(e)}")
            traceback.print_exc()
            return jsonify({
                "erro": "Erro ao gerar cobrança Pix.",
                "detalhe_tecnico": str(e)
            }), 500

        finally:
            if cursor:
                cursor.close()
            if conexao:
                conexao.close()

    # 🟢 3. WEBHOOK DE CONFIRMAÇÃO DO BANCO
    @app.route("/pagamentos/emergente/webhook_pix", methods=["POST"])
    def webhook_pix():
        data = request.get_json() or {}
        payment_id = request.args.get("data.id") or data.get("data", {}).get("id")

        if not payment_id:
            return jsonify({"status": "ignored"}), 200

        mp_access_token = os.environ.get("MERCADO_PAGO_ACCESS_TOKEN")
        headers = {"Authorization": f"Bearer {mp_access_token}"}

        res = requests.get(f"https://api.mercadopago.com/v1/payments/{payment_id}", headers=headers)
        if res.status_code == 200:
            payment_info = res.json()
            status_pagamento = payment_info.get("status")

            if status_pagamento == "approved":
                conexao = conectar_banco()
                cursor = conexao.cursor()
                try:
                    cursor.execute("SELECT passageiro_cpf, corrida_id FROM debitos_passageiros WHERE payment_id = %s", (str(payment_id),))
                    debito = cursor.fetchone()

                    if debito:
                        passageiro_cpf, corrida_id = debito[0], debito[1]

                        if corrida_id:
                            cursor.execute("UPDATE corridas_emergentes SET pago = TRUE WHERE id = %s", (corrida_id,))
                        
                        cursor.execute("UPDATE debitos_passageiros SET status = 'aprovado' WHERE payment_id = %s", (str(payment_id),))
                        cursor.execute("UPDATE usuarios SET bloqueado = FALSE WHERE cpf = %s", (passageiro_cpf,))

                        conexao.commit()
                        print(f"🎉 SUCESSO! Passageiro CPF {passageiro_cpf} desbloqueado via Pix!")
                finally:
                    cursor.close()
                    conexao.close()

        return jsonify({"status": "ok"}), 200

    # 4. Pagamento Manual/Antigo do Calote
    @app.route("/pagamentos/emergente/quitar_debito/<int:corrida_id>", methods=["POST"])
    @token_requerido
    def quitar_debito(corrida_id):
        conexao = None
        cursor = None
        try:
            usuario_logado = getattr(request, "usuario_logado", {}) or {}
            passageiro_cpf = usuario_logado.get("cpf") or usuario_logado.get("usuario")

            conexao = conectar_banco()
            cursor = conexao.cursor()

            cursor.execute("UPDATE corridas_emergentes SET pago = TRUE WHERE id = %s AND passageiro_cpf = %s", (corrida_id, passageiro_cpf))
            
            cursor.execute("SELECT id FROM corridas_emergentes WHERE passageiro_cpf = %s AND pago = FALSE AND status = 'Finalizada'", (passageiro_cpf,))
            outra_devedora = cursor.fetchone()
            
            if outra_devedora is None:
                cursor.execute("UPDATE usuarios SET bloqueado = FALSE WHERE cpf = %s", (passageiro_cpf,))
                
            conexao.commit()
            return jsonify({"mensagem": "Perfil regularizado!"}), 200
        finally:
            if cursor:
                cursor.close()
            if conexao:
                conexao.close()

    # 5. Verificação de Aluguel/Mensalidade (Motorista)
    @app.route("/pagamentos/emergente/verificar_assinatura", methods=["GET"])
    @token_requerido
    def verificar_assinatura():
        return jsonify({
            "assinatura_ativa": True, 
            "mensagem": "Acesso ao radar liberado!"
        }), 200