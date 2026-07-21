from flask import jsonify, request
import uuid


def configurar_rotas_pagamento_programado(app, conectar_banco, token_requerido):

    # 1. Gera a Taxa de Reserva para bloquear a vaga na carona
    @app.route("/pagamentos/programado/gerar_taxa", methods=["POST"])
    @token_requerido
    def gerar_taxa_reserva():
        passageiro_cpf = request.usuario_logado["cpf"]
        dados = request.get_json()
        carona_id = dados.get("carona_id")
        
        # Valor fixo da reserva que vai para o dono do app
        TAXA_RESERVA_BRL = 5.00 
        
        # Simulador de geração de código PIX Copia e Cola (No futuro, plugar API Mercado Pago/Asaas)
        codigo_pix_falso = f"00020101021126580014br.gov.bcb.pix0136{uuid.uuid4()}5204000053039865404{TAXA_RESERVA_BRL}5802BR5915TRANSP INTERIOR6009SAO PAULO62070503***6304ABCD"
        
        return jsonify({
            "mensagem": "Pix gerado com sucesso.",
            "valor": TAXA_RESERVA_BRL,
            "codigo_pix_copia_cola": codigo_pix_falso
        }), 200
        
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